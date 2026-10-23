import os
import re
import json
import queue
import asyncio
import threading
from datetime import date
import anthropic
import openai
import cohere
from google import genai
from google.genai import types as genai_types
from flask import Blueprint, request, jsonify, g, Response

from routes.auth import require_auth
from services import supabase_service, embedding_service
from services import rag_service
from services import grounding

chat_bp = Blueprint("chat", __name__)

GEMINI_MODEL = "gemini-2.5-flash"
TIMEOUT = 300
# Claude: thinking is on for every model we offer, and stays on. It is sent
# explicitly as adaptive (the model decides how far to think) with effort
# "high": Opus 5.5 and Haiku 5.5 default to "medium", one level below the
# rest, and research answers are where depth pays. Thinking shares max_tokens
# with the answer, so the floor leaves room for both; it is a ceiling, not a
# charge. (Anthropic's guidance for non-streaming calls is ~16,000.)
ANTHROPIC_MIN_MAX_TOKENS = 16000
ANTHROPIC_EFFORT = "high"
# The current web-search tool, on the models that support it; the others take
# the basic version.
ANTHROPIC_SEARCH_TOOL = {"claude-opus-5-5": "web_search_20260209", "claude-sonnet-5-5": "web_search_20260209",
                         "claude-opus-5": "web_search_20260209", "claude-sonnet-5": "web_search_20260209"}
# Gemini 2.5+ thinks before answering and the reasoning is billed against
# max_output_tokens, same as Claude. Measured: a 3-part biomedical question
# spent 860 of 900 tokens thinking and returned a 36-token truncated answer.
GEMINI_MIN_MAX_TOKENS = 32768   # was 8,192: a long list was cut off mid-citation

# Same reasoning-budget problem on the OpenAI-compatible providers: Qwen 3.x
# and the DeepSeek reasoning tiers spend output tokens thinking before any
# visible text, so a low cap yields an empty answer rather than a short one.
# Measured on a five-part biomedical question: Qwen 3.7 Plus spent 6,522
# output tokens and DeepSeek V4 exhausted 8,192 without emitting an answer,
# so the floor has to clear the thinking with room to spare.
OPENAI_LIKE_MIN_MAX_TOKENS = 16384
GEMINI_RETRY_WAIT = 15

# ── Models ─────────────────────────────────────────────────────────────────
# One table for every model the site offers. Keys are permanent: they are
# written into validations.model and usage_logs, so a key is never renamed or
# pointed at a different model — a new model gets a new key.
#
# Every id was checked against the provider's live model list on 2026-10-22.
# Retired keys (claude = Sonnet 4.6, gpt4 = GPT-4o, deepseek, qwen = Qwen 2.5,
# gemini35, opus48, fable5, gpt56sol, qwen38max, deepseek_flash) are no longer
# offered; the frontend keeps their names so old chats and statistics still
# read correctly. Retired because: superseded by a newer model of the same
# line; gemini-3.5-flash is served as 3.6; deepseek-chat is served as
# deepseek-flash, which never answered within the timeout; qwen/qwen3.8-max is
# no longer listed by OpenRouter.
#
# provider → which caller and which user key ("user_key", see below) applies.
# Gemini Pro and Gemini 4 Argon need a paid Google plan (429 on a free key).
MODELS = {
    # Anthropic
    "opus55":   {"provider": "anthropic", "id": "claude-opus-5-5"},
    "sonnet55": {"provider": "anthropic", "id": "claude-sonnet-5-5"},
    "haiku55":  {"provider": "anthropic", "id": "claude-haiku-5-5"},
    "fable51":  {"provider": "anthropic", "id": "claude-fable-5-1"},
    "opus5":    {"provider": "anthropic", "id": "claude-opus-5"},
    "sonnet5":  {"provider": "anthropic", "id": "claude-sonnet-5"},
    # OpenAI — all take max_completion_tokens rather than max_tokens
    "gpt61sol":  {"provider": "openai", "id": "gpt-6.1-sol"},
    "gpt6astra": {"provider": "openai", "id": "gpt-6-astra"},
    "gpt6luna":  {"provider": "openai", "id": "gpt-6-luna"},
    "gpt55":     {"provider": "openai", "id": "gpt-5.5"},
    "gpt54mini": {"provider": "openai", "id": "gpt-5.4-mini"},
    # DeepSeek
    "deepseek_pro": {"provider": "deepseek", "id": "deepseek-v4-pro"},
    # Google — web search only on 2.5 Flash
    "gemini38": {"provider": "google", "id": "gemini-3.8-flash"},
    "gemini37": {"provider": "google", "id": "gemini-3.7-flash"},
    "gemini36": {"provider": "google", "id": "gemini-3.6-flash"},
    "gemini":   {"provider": "google", "id": "gemini-2.5-flash", "search": True},
    # Qwen via OpenRouter (paid credit) and via Groq (free tier)
    "qwen38flash":   {"provider": "openrouter", "id": "qwen/qwen3.8-flash"},
    "qwen38max0902": {"provider": "openrouter", "id": "qwen/qwen3.8-max-0902"},
    "qwen37plus":    {"provider": "openrouter", "id": "qwen/qwen3.7-plus"},
    "qwen36flash":   {"provider": "openrouter", "id": "qwen/qwen3.6-flash"},
    "groq_qwen38":   {"provider": "groq", "id": "qwen/qwen3.8-27b"},
    # Others. "groq" was Llama 3.3 before 2026-10-01 (Groq retired it); rows
    # from before then should be analysed separately.
    "groq":   {"provider": "groq", "id": "openai/gpt-oss-120b"},
    "cohere": {"provider": "cohere", "id": "command-a-plus-05-2026"},
}

# The user_keys entry (from the browser's API-key settings) that pays for each
# provider's models, and the server's own key as the fallback.
USER_KEY = {"anthropic": "claude", "openai": "gpt4", "deepseek": "deepseek",
            "google": "gemini", "openrouter": "qwen", "groq": "groq", "cohere": "cohere"}
SERVER_KEY = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
              "deepseek": "DEEPSEEK_API_KEY", "google": "GEMINI_API_KEY",
              "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY",
              "cohere": "COHERE_API_KEY"}
OPENAI_COMPATIBLE_URL = {"deepseek": "https://api.deepseek.com",
                         "openrouter": "https://openrouter.ai/api/v1",
                         "groq": "https://api.groq.com/openai/v1",
                         "openai": None}

PROVIDER_KEY_FOR_MODEL = {k: USER_KEY[m["provider"]] for k, m in MODELS.items()}
VALID_MODELS = set(MODELS)
# Anthropic and OpenAI bill per token on the project's keys, so they are metered.
GATED_MODELS = {k for k, m in MODELS.items() if m["provider"] in ("anthropic", "openai")}
FREE_LIMIT      = 5                     # messages/user/day on backend keys
ADMIN_EMAILS    = [e.strip().lower() for e in
                   (os.getenv("ADMIN_EMAILS") or os.getenv("ADMIN_EMAIL", "chitrashenshah@gmail.com")).split(",")
                   if e.strip()]

# In-memory caches (never persisted across restarts).
# _quota_cache:  {(user_id, model): {"date": str, "blocked": bool}}  — per-model daily quota
# _granted_cache:{user_id: {"date": str, "granted": bool}}           — admin-granted status
_quota_cache: dict = {}
_granted_cache: dict = {}
# _session_docs_cache:   {session_id: bool}  — session has any documents at all
# _session_chunks_cache: {session_id: bool}  — session has embedded chunks (RAG)
_session_docs_cache: dict = {}
_session_chunks_cache: dict = {}

MAX_MODELS_PER_REQUEST = 5    # the site's five boxes (Chat.jsx MAX_BOXES)
MAX_DOC_CONTEXT_CHARS = 80_000  # cap total injected document text


def _compose_mode_prompt(mode: str, system_prompt: str | None) -> str | None:
    """Prepend the hidden biomedical baseline when in biomedical mode."""
    if mode == "biomedical":
        if system_prompt:
            return f"{grounding.BIOMEDICAL_BASE_PROMPT}\n\n{system_prompt}"
        return grounding.BIOMEDICAL_BASE_PROMPT
    return system_prompt


def _is_granted_cached(user_id: str, today: str) -> bool:
    """Granted status, cached per day (one DB call per user per day)."""
    c = _granted_cache.get(user_id)
    if c and c.get("date") == today:
        return c.get("granted", False)
    try:
        granted = supabase_service.is_user_granted(user_id)
    except Exception:
        granted = False
    _granted_cache[user_id] = {"date": today, "granted": granted}
    return granted


def _model_quota_ok(user_id: str, today: str, model: str) -> bool:
    """
    True if the user is under their daily limit for this specific model.
    Increments the per-model counter while under limit. Once over the limit the
    'blocked' state is cached so no further DB calls happen that day.
    """
    key = (user_id, model)
    c = _quota_cache.get(key)
    if c and c.get("date") == today and c.get("blocked"):
        return False
    try:
        count = supabase_service.check_and_increment_user_quota(user_id, today, model)
    except Exception:
        return True  # fail open — never block on DB error
    if count > FREE_LIMIT:
        _quota_cache[key] = {"date": today, "blocked": True}
        return False
    _quota_cache[key] = {"date": today, "blocked": False}
    return True


def _gated_blocked_models(user: dict, user_keys: dict, gated_requested: list) -> set:
    """
    Return the set of gated models the user cannot use right now.
    Each gated model has its OWN 5/day limit — they are counted independently.
    """
    if not gated_requested:
        return set()
    if not user:
        return set(gated_requested)

    user_id    = user.get("id", "")
    user_email = user.get("email", "")
    today      = date.today().isoformat()

    # Admin or granted → nothing blocked
    if user_email.lower() in ADMIN_EMAILS:
        return set()
    if _is_granted_cached(user_id, today):
        return set()

    blocked = set()
    for m in gated_requested:
        # Own key for this model's provider → allowed. An OpenRouter key counts
        # only for the models _run_model actually sends through OpenRouter
        # (claude, gpt4). The newer Anthropic and OpenAI models always run on
        # the provider's own key, so counting it for them let any non-empty
        # "openrouter" value lift the limit on the project's keys.
        if user_keys.get(PROVIDER_KEY_FOR_MODEL.get(m, m)):
            continue
        if not _model_quota_ok(user_id, today, m):
            blocked.add(m)
    return blocked


def _gemini_is_retryable(err: str) -> bool:
    e = err.upper()
    return "503" in err or "UNAVAILABLE" in e or "429" in err or "QUOTA" in e or "RATE" in e or "RESOURCE_EXHAUSTED" in e


def _error(model: str, code: str) -> dict:
    """A model result that failed before producing usage. `code` is what the
    frontend explains to the user (API_KEY_INVALID, RATE_LIMIT, …)."""
    return {"model": model, "status": "error", "error": code, "usage": _usage()}


def _usage(input_t=0, output_t=0, cache_read=0, cache_write=0) -> dict:
    return {
        "input_tokens": int(input_t or 0),
        "output_tokens": int(output_t or 0),
        "cache_read_tokens": int(cache_read or 0),
        "cache_write_tokens": int(cache_write or 0),
    }


# ── Document context (persistent session files, injected + cached) ──────────

def _build_document_context(docs: list, session_id: str | None = None,
                            query: str = "") -> tuple[str, str]:
    """
    Build the document context for a query, as (stable, per_question).

    - No docs → ("", "").
    - Small total (< RAG threshold) → the whole document(s) as `stable`. It is
      identical on every turn, so it belongs in the system prompt and caches.
    - Large total → RAG: the passages retrieved for this question, as
      `per_question`. They change every turn, so they must go AFTER the
      history: placed before it, they changed the prompt prefix on every turn
      and no provider could cache the conversation behind them.
      Falls back to truncated full text if retrieval is unavailable.
    """
    if not docs:
        return "", ""

    total_chars = sum(len((d.get("content") or "")) for d in docs)

    # Large docs + a real question → retrieve just the relevant passages.
    if (total_chars > rag_service.RAG_THRESHOLD_CHARS and session_id and query
            and _session_chunks_cache.get(session_id) is not False):
        retrieved = _retrieve_chunks(session_id, query)
        if retrieved:
            return "", (
                "The most relevant passages from the uploaded document(s) for this "
                "question are below. Use them as your primary reference:\n\n" + retrieved
            )

    # Otherwise send the full text (capped), letting prompt caching handle repetition.
    return _full_document_context(docs), ""


def _retrieve_chunks(session_id: str, query: str) -> str:
    """Embed the query and return the top matching chunks as a framed block, or ''."""
    try:
        q_emb = embedding_service.embed_query(query)
        if not q_emb:
            return ""
        chunks = supabase_service.match_document_chunks(session_id, q_emb, rag_service.TOP_K)
        _session_chunks_cache[session_id] = bool(chunks)
        if not chunks:
            return ""
        return "\n\n".join(f"[Passage {i+1}]\n{c}" for i, c in enumerate(chunks))
    except Exception:
        return ""


# The one framing for uploaded files, whether they come from the session's
# stored documents or are attached inline (no session to store them on). The
# two paths used to describe the same files in different words.
_FILES_PREAMBLE = "Files the user uploaded, with their extracted text. Use them as reference:"


def _file_block(name: str, text: str) -> str:
    text = (text or "").strip() or "(No text could be extracted — the file may be image-based or empty.)"
    return f"=== FILE: {name or 'document'} ===\n{text}\n=== END FILE ==="


def _full_document_context(docs: list) -> str:
    """Whole stored documents as one capped block (small-document path)."""
    blocks, total = [], 0
    for d in docs:
        if not (d.get("content") or "").strip():
            continue
        block = _file_block(d.get("filename"), d["content"])
        if total + len(block) > MAX_DOC_CONTEXT_CHARS:
            blocks.append("=== [additional document text truncated to conserve tokens] ===")
            break
        blocks.append(block)
        total += len(block)
    return f"{_FILES_PREAMBLE}\n\n" + "\n\n".join(blocks) if blocks else ""


def _build_full_message(message: str, text_atts: list) -> str:
    """Freshly attached files inline in the message, when there is no session."""
    if not text_atts:
        return message
    files = "\n\n".join(_file_block(a.get("filename"), a.get("text")) for a in text_atts)
    return f"{message or 'Please read and analyze these files.'}\n\n{_FILES_PREAMBLE}\n\n{files}"


def _compose_system(system_prompt: str | None, doc_context: str) -> str | None:
    """
    Compose the system text: the system prompt, then any whole documents.
    Both are identical on every turn, so system + history form a stable prefix
    that OpenAI, DeepSeek and Gemini cache automatically. Per-question RAG
    passages are kept out of here (see _build_document_context).
    """
    parts = [p for p in (system_prompt, doc_context) if p]
    return "\n\n".join(parts) if parts else None


def _build_user_content_claude(text: str, image_atts: list):
    if not image_atts:
        return text
    content = [{
        "type": "image",
        "source": {"type": "base64", "media_type": img["mime_type"], "data": img["base64"]},
    } for img in image_atts]
    content.append({"type": "text", "text": text})
    return content


def _build_user_content_gpt(text: str, image_atts: list):
    if not image_atts:
        return text
    content = [{
        "type": "image_url",
        "image_url": {"url": f"data:{img['mime_type']};base64,{img['base64']}"},
    } for img in image_atts]
    content.append({"type": "text", "text": text})
    return content


# ── Model callers — each returns {model, status, text, usage} ───────────────

async def _call_claude(message, system_prompt, doc_context, history, image_atts, max_tokens=4096,
                       user_api_key=None, model_id="claude-sonnet-4-6", model_key="claude",
                       use_search=False):
    api_key = user_api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _error(model_key, "API_KEY_INVALID")
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        messages = [{"role": h["role"], "content": h["content"]} for h in grounding.cap_history(history)]
        if messages:
            # Cache breakpoint on the last earlier turn. Only the system prompt
            # and documents were marked before, so every follow-up paid full
            # price to resend the conversation. With this, the next turn reads
            # everything up to here from cache (Anthropic checks earlier block
            # boundaries for a matching prefix automatically).
            last = messages[-1]
            last["content"] = [{"type": "text", "text": last["content"],
                                "cache_control": {"type": "ephemeral"}}]
        messages.append({"role": "user", "content": _build_user_content_claude(message, image_atts)})

        # System prompt, then whole documents: both identical on every turn, so
        # both cache. Retrieved passages are not here; they ride in the message.
        system_blocks = []
        if system_prompt:
            system_blocks.append({"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}})
        if doc_context:
            system_blocks.append({"type": "text", "text": doc_context, "cache_control": {"type": "ephemeral"}})

        kwargs = dict(
            model=model_id,
            max_tokens=max(max_tokens, ANTHROPIC_MIN_MAX_TOKENS),
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": ANTHROPIC_EFFORT},
        )
        if use_search:
            kwargs["tools"] = [{"type": ANTHROPIC_SEARCH_TOOL.get(model_id, "web_search_20250305"),
                                "name": "web_search"}]
        if system_blocks:
            kwargs["system"] = system_blocks

        response = await asyncio.wait_for(client.messages.create(**kwargs), timeout=TIMEOUT)
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        u = response.usage
        usage = _usage(
            getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
            getattr(u, "cache_read_input_tokens", 0), getattr(u, "cache_creation_input_tokens", 0),
        )
        served = getattr(response, "model", None)
        stop = getattr(response, "stop_reason", None)

        # Safety classifiers can decline a request: HTTP 200, empty content,
        # stop_reason "refusal". Reported as success before, which rendered as a
        # blank panel with no explanation.
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            return {"model": model_key, "status": "error", "error": "REFUSED",
                    "refusal_category": cat, "usage": usage, "served_model": served}

        if not text.strip():
            # Thinking and the visible answer share one max_tokens budget, so a
            # hard question can spend the whole budget reasoning and leave no
            # text behind. Say so rather than showing an empty box.
            err = "TRUNCATED_BEFORE_ANSWER" if stop == "max_tokens" else "EMPTY_RESPONSE"
            return {"model": model_key, "status": "error", "error": err,
                    "usage": usage, "served_model": served}

        # `response.model` is the server's own report of which model ran. Models
        # are unreliable narrators about their own identity, so this is the only
        # trustworthy provenance for a scored answer.
        return {"model": model_key, "status": "ok", "text": text, "usage": usage,
                "served_model": served, "stop_reason": stop}
    except asyncio.TimeoutError:
        return {"model": model_key, "status": "TIMED_OUT", "error": "TIMEOUT", "usage": _usage()}
    except anthropic.AuthenticationError:
        return _error(model_key, "API_KEY_INVALID")
    except anthropic.RateLimitError:
        return _error(model_key, "RATE_LIMIT")
    except anthropic.BadRequestError as e:
        msg = str(e)
        if "usage limit" in msg.lower():
            # Surface the reset date the API gives us — this is a configured
            # spend cap, not an empty balance, so the user needs to know when it
            # lifts (or that they can raise it now).
            when = re.search(r"regain access on ([0-9]{4}-[0-9]{2}-[0-9]{2}[^\"']*)", msg)
            return {"model": model_key, "status": "error", "error": "USAGE_LIMIT",
                    "limit_resets": when.group(1).strip() if when else None,
                    "usage": _usage()}
        if "credit balance" in msg.lower():
            # An empty prepaid balance, unlike the spend cap above. Shown raw it
            # was a 200-character provider message in the answer box.
            return _error(model_key, "INSUFFICIENT_CREDITS")
        return _error(model_key, msg[:200])
    except Exception as e:
        return _error(model_key, str(e)[:200])


def _openai_usage(response) -> dict:
    try:
        u = response.usage
        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        return _usage(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), cached, 0)
    except Exception:
        return _usage()


# Thinking stays on for every model. It is the expensive part — measured on
# one literature question, DeepSeek V4 Pro billed ~6,000 output tokens of
# reasoning to show 1,054 characters — but reasoning is what synthesis
# questions need, and this project asks synthesis questions. Cost is saved
# through caching instead, never by switching thinking off.

# Reasoning models on these providers think before answering, and the thinking
# shares the output budget. Uncapped, Qwen 3.8 Flash thought past the 300s
# timeout and Command A+ spent all 4,096 tokens thinking, so both returned
# nothing on a research question (measured 2026-10-21). Capped at 6,000, both
# answered in 40–90s. Sent as extra_body; models that do not reason ignore it.
REASONING_BUDGET = 6000
REASONING_CAP: dict = {"openrouter": {"reasoning": {"max_tokens": REASONING_BUDGET}}}


async def _call_openai_like(model_key, model_name, base_url, api_key,
                            message, system_prompt, doc_context, history, image_atts, max_tokens,
                            token_param="max_tokens", provider=None):
    if not api_key:
        return _error(model_key, "API_KEY_INVALID")
    try:
        client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url) if base_url \
            else openai.AsyncOpenAI(api_key=api_key)
        messages = []
        composed = _compose_system(system_prompt, doc_context)
        if composed:
            messages.append({"role": "system", "content": composed})
        for h in grounding.cap_history(history):
            messages.append({"role": h["role"], "content": h["content"]})
        user_content = _build_user_content_gpt(message, image_atts) if image_atts else message
        messages.append({"role": "user", "content": user_content})

        # Newer OpenAI models reject `max_tokens` and require
        # `max_completion_tokens`. Try the requested spelling, then swap once on
        # the specific 400 so a future model doesn't need a code change here.
        # Qwen 3.x and the DeepSeek reasoning tiers think before answering, and
        # the thinking is billed against this same budget. At 4096 a hard
        # question can be entirely consumed by reasoning, returning an empty
        # answer that looked like "the model said nothing". The cap is not a
        # reservation — raising it costs nothing unless the tokens are used.
        cap = REASONING_CAP.get(provider or "")
        extra = {"extra_body": cap} if cap else {}
        budget = max(max_tokens, OPENAI_LIKE_MIN_MAX_TOKENS)

        async def _create(param):
            return await asyncio.wait_for(
                client.chat.completions.create(
                    model=model_name, messages=messages, **{param: budget}, **extra),
                timeout=TIMEOUT,
            )

        try:
            response = await _create(token_param)
        except openai.BadRequestError as e:
            other = "max_completion_tokens" if token_param == "max_tokens" else "max_tokens"
            if other in str(e):
                response = await _create(other)
            else:
                raise
        except Exception as e:
            # OpenRouter refuses the request outright when the account cannot
            # cover the requested cap ("can only afford N tokens"). The floor
            # above is what provokes this, so honour the number it names rather
            # than failing a request the account could still pay for.
            afford = re.search(r"can only afford (\d+)", str(e))
            if afford and "402" in str(e):
                budget = max(256, int(afford.group(1)))
                response = await _create(token_param)
            else:
                raise
        # OpenRouter/free tiers sometimes return an error-shaped body (HTTP 200 with
        # choices=None or an embedded error) instead of raising. Handle gracefully.
        choices = getattr(response, "choices", None)
        if not choices:
            err_obj = getattr(response, "error", None)
            msg = ""
            if isinstance(err_obj, dict):
                msg = str(err_obj.get("message", "")).lower()
            if "rate" in msg or "429" in msg or "limit" in msg:
                return _error(model_key, "RATE_LIMIT")
            return _error(model_key, "MODEL_UNAVAILABLE")
        # An exhausted budget on a reasoning model returns HTTP 200 with empty
        # content. Reported as "ok" that renders as a blank box with nothing to
        # explain it — the Claude and Gemini paths already distinguish these,
        # and this one has to as well or a silent blank is indistinguishable
        # from a model that genuinely had nothing to say.
        text = (choices[0].message.content or "").strip()
        if not text:
            finish = getattr(choices[0], "finish_reason", None)
            return {"model": model_key, "status": "error",
                    "error": "TRUNCATED_BEFORE_ANSWER" if finish == "length" else "EMPTY_RESPONSE",
                    "usage": _openai_usage(response),
                    "served_model": getattr(response, "model", None)}
        return {"model": model_key, "status": "ok",
                "text": text, "usage": _openai_usage(response),
                "served_model": getattr(response, "model", None)}
    except asyncio.TimeoutError:
        return {"model": model_key, "status": "TIMED_OUT", "error": "TIMEOUT", "usage": _usage()}
    except openai.AuthenticationError:
        return _error(model_key, "API_KEY_INVALID")
    except openai.RateLimitError:
        return _error(model_key, "RATE_LIMIT")
    except Exception as e:
        err = str(e)
        if "402" in err or "requires more credits" in err:
            return {"model": model_key, "status": "error",
                    "error": "INSUFFICIENT_CREDITS", "usage": _usage()}
        low = err.lower()
        # OpenRouter/provider errors arrive as a big JSON string — map to clean codes
        if "429" in err or "rate-limit" in low or "rate limit" in low:
            return _error(model_key, "RATE_LIMIT")
        if "unauthorized" in low or "401" in err or "no auth" in low:
            return _error(model_key, "API_KEY_INVALID")
        if "does not support" in low or "no endpoints" in low or "not support" in low:
            return _error(model_key, "MODEL_UNAVAILABLE")
        return _error(model_key, err[:200])


async def _call_gemini(message, system_prompt, doc_context, history, image_atts, max_tokens=4096,
                       user_api_key=None, model_id=None, model_key="gemini", use_search=False):
    api_key = user_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _error(model_key, "API_KEY_INVALID")
    try:
        client = genai.Client(api_key=api_key)
        contents = []
        for h in grounding.cap_history(history):
            gemini_role = "model" if h.get("role") == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": h["content"]}]})
        parts = [{"inline_data": {"mime_type": img["mime_type"], "data": img["base64"]}} for img in image_atts]
        parts.append({"text": message})
        contents.append({"role": "user", "parts": parts})

        # Search grounding is per-model: on a free-tier key only Gemini 2.5
        # Flash carries the grounding quota. Sending the tool to a 3.x model
        # returns 429 even though the model itself answers fine without it.
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max(max_tokens, GEMINI_MIN_MAX_TOKENS))
        if use_search:
            config.tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
        composed = _compose_system(system_prompt, doc_context)
        if composed:
            config.system_instruction = composed

        loop = asyncio.get_event_loop()

        async def _do_call():
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: client.models.generate_content(
                    model=model_id or GEMINI_MODEL, contents=contents, config=config)),
                timeout=TIMEOUT,
            )

        try:
            response = await _do_call()
        except Exception as first_err:
            if _gemini_is_retryable(str(first_err)):
                await asyncio.sleep(GEMINI_RETRY_WAIT)
                response = await _do_call()
            else:
                raise first_err

        um = getattr(response, "usage_metadata", None)
        usage = _usage(
            getattr(um, "prompt_token_count", 0) if um else 0,
            getattr(um, "candidates_token_count", 0) if um else 0,
            getattr(um, "cached_content_token_count", 0) if um else 0, 0,
        )
        served = getattr(response, "model_version", None) or model_id or GEMINI_MODEL
        text = response.text or ""

        # Thinking shares the output budget, so a hard question can exhaust it
        # before any answer is written. This used to return status "ok" with a
        # sentence-long reply — a silently truncated answer is worse than an
        # error, because it would be scored as the model's real attempt.
        finish = ""
        try:
            finish = str(response.candidates[0].finish_reason or "")
        except Exception:
            pass
        if "MAX_TOKENS" in finish.upper():
            if not text.strip():
                return {"model": model_key, "status": "error",
                        "error": "TRUNCATED_BEFORE_ANSWER",
                        "usage": usage, "served_model": served}
            return {"model": model_key, "status": "ok", "text": text, "usage": usage,
                    "served_model": served, "truncated": True}

        if not text.strip():
            return {"model": model_key, "status": "error", "error": "EMPTY_RESPONSE",
                    "usage": usage, "served_model": served}

        return {"model": model_key, "status": "ok", "text": text, "usage": usage,
                "served_model": served}
    except asyncio.TimeoutError:
        return {"model": model_key, "status": "TIMED_OUT", "error": "TIMEOUT", "usage": _usage()}
    except Exception as e:
        err = str(e)
        if "503" in err or "UNAVAILABLE" in err.upper():
            return {"model": model_key, "status": "error", "error": "SERVICE_UNAVAILABLE",
                    "message": "Gemini is experiencing high demand. Try again in a moment.", "usage": _usage()}
        if "429" in err or "QUOTA" in err.upper() or "RATE" in err.upper():
            return _error(model_key, "RATE_LIMIT")
        if "API_KEY" in err.upper() or "INVALID" in err.upper():
            return _error(model_key, "API_KEY_INVALID")
        return _error(model_key, err)


async def _call_cohere(message, system_prompt, doc_context, history, image_atts, max_tokens=4096,
                       user_api_key=None, model_id="command-a-plus-05-2026", model_key="cohere"):
    api_key = user_api_key or os.getenv("COHERE_API_KEY")
    if not api_key:
        return _error(model_key, "API_KEY_INVALID")
    try:
        client = cohere.AsyncClientV2(api_key=api_key)
        messages = []
        composed = _compose_system(system_prompt, doc_context)
        if composed:
            messages.append({"role": "system", "content": composed})
        for h in grounding.cap_history(history):
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        response = await asyncio.wait_for(
            client.chat(model=model_id, messages=messages,
                        max_tokens=max(max_tokens, OPENAI_LIKE_MIN_MAX_TOKENS),
                        thinking={"type": "enabled", "token_budget": REASONING_BUDGET}),
            timeout=TIMEOUT,
        )
        usage = _usage()
        try:
            bu = response.usage.tokens
            usage = _usage(getattr(bu, "input_tokens", 0), getattr(bu, "output_tokens", 0))
        except Exception:
            pass
        # The command-a line reasons before answering, so content[0] can be a
        # thinking block with no .text at all — taking it blindly raised
        # "'ThinkingAssistantMessageResponseContentItem' object has no
        # attribute 'text'". Join the text items and ignore the reasoning.
        parts = [c.text for c in (response.message.content or [])
                 if getattr(c, "text", None)]
        text = "\n".join(parts).strip()
        if not text:
            return {"model": model_key, "status": "error",
                    "error": "EMPTY_RESPONSE", "usage": usage}
        return {"model": model_key, "status": "ok", "text": text, "usage": usage}
    except asyncio.TimeoutError:
        return {"model": model_key, "status": "TIMED_OUT", "error": "TIMEOUT", "usage": _usage()}
    except Exception as e:
        err = str(e)
        if "401" in err or "unauthorized" in err.lower():
            return _error(model_key, "API_KEY_INVALID")
        if "429" in err or "rate" in err.lower():
            return _error(model_key, "RATE_LIMIT")
        return _error(model_key, err)




async def _run_model(model, message, system_prompt, doc_context, history, image_atts, max_tokens, user_keys=None, web_search=False):
    """Run one model from MODELS, on the user's own key for its provider if
    they gave one, otherwise on the server's."""
    spec = MODELS.get(model)
    if not spec:
        return _error(model, "UNKNOWN_MODEL")
    provider, model_id = spec["provider"], spec["id"]
    user_keys = user_keys or {}
    key = (user_keys.get(USER_KEY[provider])
           # keys saved under the old "OpenRouter" setting still pay for Qwen
           or (user_keys.get("openrouter") if provider == "openrouter" else None)
           or os.getenv(SERVER_KEY[provider]))

    if provider == "anthropic":
        return await _call_claude(message, system_prompt, doc_context, history, image_atts,
                                  max_tokens, user_api_key=key, model_id=model_id,
                                  model_key=model, use_search=web_search)
    if provider == "google":
        return await _call_gemini(message, system_prompt, doc_context, history, image_atts,
                                  max_tokens, user_api_key=key, model_id=model_id, model_key=model,
                                  use_search=web_search and spec.get("search", False))
    if provider == "cohere":
        return await _call_cohere(message, system_prompt, doc_context, history, image_atts,
                                  max_tokens, user_api_key=key, model_id=model_id, model_key=model)
    # OpenAI and the OpenAI-compatible APIs. Only OpenAI itself takes images.
    return await _call_openai_like(model, model_id, OPENAI_COMPATIBLE_URL[provider], key,
                                   message, system_prompt, doc_context, history,
                                   image_atts if provider == "openai" else [], max_tokens,
                                   token_param="max_completion_tokens" if provider == "openai" else "max_tokens",
                                   provider=provider)


def _persist_and_load_docs(session_id, user, text_atts) -> list:
    """
    Store any freshly-attached text files on the session, then return all docs.
    Large docs are also chunked + embedded for RAG retrieval. Skips the DB read
    for sessions known to have no documents (avoids a round-trip every message).
    """
    if not session_id:
        return []

    user_id = (user or {}).get("id")
    new_docs = False
    for att in text_atts:
        extracted = (att.get("text") or "").strip()
        if not extracted:
            continue
        new_docs = True
        try:
            supabase_service.save_document(
                session_id, user_id,
                att.get("filename", "document"), att.get("file_type", "file"), extracted,
            )
        except Exception:
            pass
        # Chunk + embed EVERY doc so retrieval can cover all of them — including a
        # small doc that shares a session with a large one. Whether we actually
        # retrieve vs. send whole is decided later by the session's TOTAL size.
        _index_document(session_id, user_id, extracted)

    if new_docs:
        _session_docs_cache[session_id] = True

    if not new_docs and _session_docs_cache.get(session_id) is False:
        return []

    try:
        docs = supabase_service.get_documents(session_id)
        _session_docs_cache[session_id] = len(docs) > 0
        return docs
    except Exception:
        return []


def _index_document(session_id: str, user_id: str | None, text: str) -> None:
    """Chunk + embed a document and store its vectors. Best-effort — any failure
    just means we fall back to sending the (truncated) full text instead."""
    try:
        chunks = rag_service.chunk_text(text)
        if not chunks:
            return
        embeddings = embedding_service.embed_texts(chunks)
        if not embeddings:
            return
        supabase_service.save_document_chunks(session_id, user_id, chunks, embeddings)
        _session_chunks_cache[session_id] = True
    except Exception:
        pass


def _log_usage(results: dict, session_id, user, mode):
    rows = []
    for model, r in results.items():
        u = r.get("usage") or {}
        rows.append({
            "session_id": session_id,
            "user_id": (user or {}).get("id"),
            "email": (user or {}).get("email"),
            "model": model,
            "mode": mode,
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "cache_read_tokens": u.get("cache_read_tokens", 0),
            "cache_write_tokens": u.get("cache_write_tokens", 0),
            "status": r.get("status"),
        })
    supabase_service.log_usage(rows)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stream_models(models, message, full_message, system_prompt, doc_context,
                   histories, history, image_atts, max_tokens, session_id, user, mode,
                   user_keys=None, gated_models=None, web_search=False, literature=None,
                   missed=None):
    """
    Generator of SSE frames. A background thread runs all model calls
    concurrently in its own event loop and pushes each result onto a
    thread-safe queue the moment it completes; this generator yields a frame
    per result so the client renders each panel as soon as it's ready.

    gated_models: subset of `models` (Claude and GPT) that need a per-model daily
    quota check. That check is a DB call, so it runs in a thread-pool executor —
    concurrently with the free models, which start immediately and never wait on it.
    A gated model over its limit emits DAILY_LIMIT_REACHED without calling any provider.
    """
    gated_set = set(gated_models or [])
    q: "queue.Queue" = queue.Queue()
    results: dict = {}

    literature = literature or {}

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        search_for = grounding.literature_searcher(
            message, literature, grounding.previous_question(histories, history))

        async def run_one(m):
            # Gated models: run the quota check off the event loop so it doesn't
            # stall the free models that are streaming concurrently.
            if m in gated_set:
                blocked = await loop.run_in_executor(
                    None, _gated_blocked_models, user, user_keys, [m]
                )
                if blocked:
                    res = _error(m, "DAILY_LIMIT_REACHED")
                    results[m] = res
                    q.put(("model", m, res))
                    return
            msg, papers_given = await grounding.grounded_message(m, full_message, literature, search_for, missed)
            try:
                res = await _run_model(m, msg, system_prompt, doc_context,
                                       histories.get(m, history), image_atts, max_tokens, user_keys,
                                       web_search=web_search)
            except Exception as e:
                res = _error(m, str(e))
            if papers_given:
                res["grounding"] = papers_given
            results[m] = res
            q.put(("model", m, res))

        try:
            loop.run_until_complete(asyncio.gather(*[run_one(m) for m in models]))
        finally:
            loop.close()
            q.put(("done", None, None))

    threading.Thread(target=worker, daemon=True).start()

    while True:
        kind, m, payload = q.get()
        if kind == "done":
            break
        client_res = {k: v for k, v in payload.items() if k != "usage"}
        yield _sse("model", {"model": m, "response": client_res})

    # Finalize once all models are done (best-effort; never breaks the stream).
    try:
        _log_usage(results, session_id, user, mode)
    except Exception:
        pass
    if session_id and message:
        supabase_service.append_turn(session_id, "user", message)

    yield _sse("end", {})


# ── Routes ─────────────────────────────────────────────────────────────────

@chat_bp.route("/ask", methods=["POST", "OPTIONS"])
@require_auth
def ask():
    """Fan out one question to several models server-side and return all results."""
    data = request.get_json(silent=True) or {}
    user = getattr(g, "user", None)

    message     = (data.get("message") or "").strip()
    models      = data.get("models") or ["gemini"]
    mode        = data.get("mode", "normal")
    # Opt-in: the search tool adds ~2,200 input tokens to every message and each
    # search is billed, so it stays off unless the request asks for it.
    web_search  = bool(data.get("web_search"))
    session_id  = data.get("session_id")
    history     = data.get("history", [])         # shared fallback history
    histories   = data.get("histories") or {}      # optional per-model history
    attachments = data.get("attachments", [])

    user_keys  = data.get("user_keys") or {}   # {model: api_key} from frontend localStorage
    literature_opts = data.get("literature") or {}   # {model: {"db": …, "dates": …}}
    missed      = data.get("missed_questions") or {}  # {model: [earlier questions it never saw]}

    models = list(dict.fromkeys(m for m in models if m in VALID_MODELS))
    if not models:
        return jsonify({"error": "NO_VALID_MODELS"}), 400
    if len(models) > MAX_MODELS_PER_REQUEST:
        return jsonify({"error": "TOO_MANY_MODELS",
                        "message": f"At most {MAX_MODELS_PER_REQUEST} models per question"}), 400
    literature = grounding.parse_literature(literature_opts, models)
    if not message and not attachments:
        return jsonify({"error": "MISSING_MESSAGE"}), 400

    # Each Claude and GPT model has its OWN 5/day limit. The per-model quota
    # check is a DB call, so we defer it into the streaming worker where it runs
    # concurrently with the free models (which never block on it).
    gated_requested = [m for m in models if m in GATED_MODELS]

    system_prompt = (data.get("system_prompt") or "").strip() or None
    system_prompt = _compose_mode_prompt(mode, system_prompt)
    max_tokens = 4096

    text_atts  = [a for a in attachments if a.get("file_type") != "image"]
    image_atts = [a for a in attachments if a.get("file_type") == "image"]

    docs = _persist_and_load_docs(session_id, user, text_atts)
    doc_context, passages = _build_document_context(docs, session_id, message)
    # Docs are delivered via doc_context (persisted + retrieved). Only inline them
    # into the message when there's no session to persist to — avoids sending the
    # document text twice.
    full_message = message if (session_id and docs) else _build_full_message(message, text_atts)
    if passages:
        # Only this turn sees them: history carries the question alone, so old
        # passages never pile up in later turns.
        full_message = f"{passages}\n\n---\n\n{full_message}"

    # Streaming path: emit each model's result over SSE as it completes. Gated
    # quota checks happen inside the worker, concurrently with free models.
    if data.get("stream"):
        return Response(
            _stream_models(models, message, full_message, system_prompt, doc_context,
                           histories, history, image_atts, max_tokens, session_id, user, mode,
                           user_keys, gated_requested, web_search=web_search,
                           literature=literature, missed=missed),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming path: compute blocked set up-front (rarely used by the UI).
    blocked_models = _gated_blocked_models(user, user_keys, gated_requested)
    run_models = [m for m in models if m not in blocked_models]

    async def run():
        search_for = grounding.literature_searcher(
            message, literature, grounding.previous_question(histories, history))

        async def one(m):
            msg, papers_given = await grounding.grounded_message(m, full_message, literature, search_for, missed)
            res = await _run_model(m, msg, system_prompt, doc_context,
                                   histories.get(m, history), image_atts, max_tokens, user_keys,
                                   web_search=web_search)
            return {**res, "grounding": papers_given} if papers_given else res

        done = await asyncio.gather(*[one(m) for m in run_models], return_exceptions=True)
        out = {}
        for m, res in zip(run_models, done):
            if isinstance(res, Exception):
                out[m] = _error(m, str(res))
            else:
                out[m] = res
        # Emit a limit error for each blocked gated model
        for m in blocked_models:
            out[m] = _error(m, "DAILY_LIMIT_REACHED")
        return out

    results = asyncio.run(run())

    try:
        _log_usage(results, session_id, user, mode)
    except Exception:
        pass

    # Persist the user turn atomically (no-op for non-uuid session ids)
    if session_id and message:
        supabase_service.append_turn(session_id, "user", message)

    # Strip usage from the client-facing payload (keep responses lean)
    responses = {m: {k: v for k, v in r.items() if k != "usage"} for m, r in results.items()}
    usage_totals = {m: r.get("usage", _usage()) for m, r in results.items()}
    return jsonify({"status": "ok", "responses": responses, "usage": usage_totals}), 200
