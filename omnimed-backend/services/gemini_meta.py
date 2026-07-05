# Gemini meta-analysis service — used by /compare to synthesize all model outputs.

import os
import re
import json
import asyncio
from google import genai

MODEL = "gemini-2.5-flash"
TIMEOUT = 120  # longer timeout for meta-analysis synthesis
RATE_LIMIT_RETRY_WAIT = 35  # seconds to wait before retrying on 429


def _load_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_json_from_response(text: str) -> dict | None:
    match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            return None
    return None


def _is_retryable(err: str) -> bool:
    return ("429" in err or "QUOTA" in err.upper() or "RATE" in err.upper()
            or "RESOURCE_EXHAUSTED" in err.upper()
            or "503" in err or "UNAVAILABLE" in err.upper())


async def call_gemini_meta(user_message: str, prompt_path: str | None, system_prompt_override: str | None = None) -> dict:
    """
    Send all model comparison stubs to Gemini for meta-analysis.
    Returns unified comparison table, conflict analysis, reliability scores,
    and wet-lab summary cards.
    On 429 rate limit, waits 35 seconds and retries once before returning error.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"model": "gemini_meta", "error": "API_KEY_INVALID", "status": "error"}

    if system_prompt_override is not None:
        system_prompt = system_prompt_override
    elif prompt_path:
        system_prompt = _load_prompt(prompt_path)
    else:
        system_prompt = ""
    client = genai.Client(api_key=api_key)
    full_prompt = f"{system_prompt}\n\n{user_message}".strip() if system_prompt else user_message

    async def _single_call() -> tuple[str, dict | None]:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=MODEL,
                    contents=full_prompt,
                ),
            ),
            timeout=TIMEOUT,
        )
        raw = response.text
        parsed = _extract_json_from_response(raw)
        return raw, parsed

    try:
        try:
            raw, parsed = await _single_call()
        except Exception as e:
            if _is_retryable(str(e)):
                await asyncio.sleep(RATE_LIMIT_RETRY_WAIT)
                raw, parsed = await _single_call()
            else:
                raise

        # Retry once on JSON parse failure
        if parsed is None:
            raw, parsed = await _single_call()

        if parsed is None:
            return {
                "model": "gemini_meta",
                "status": "JSON_PARSE_FAILED",
                "raw_response": raw,
                "parsed_json": None,
            }

        return {"model": "gemini_meta", "status": "ok", "raw_response": raw, "parsed_json": parsed}

    except asyncio.TimeoutError:
        return {"model": "gemini_meta", "status": "TIMED_OUT", "error": "TIMEOUT",
                "message": "Meta-analysis model did not respond within 120 seconds"}
    except Exception as e:
        err = str(e)
        if _is_retryable(err):
            return {"model": "gemini_meta", "status": "error", "error": "RATE_LIMIT", "retry_after": 60}
        return {"model": "gemini_meta", "status": "error", "error": err}
