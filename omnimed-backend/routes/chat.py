import os
import re
import json
import queue
import asyncio
import threading
import anthropic
import openai
import cohere
from google import genai
from google.genai import types as genai_types
from flask import Blueprint, request, jsonify, g, Response

from routes.auth import require_auth
from services import supabase_service, embedding_service
from services import rag_service

chat_bp = Blueprint("chat", __name__)

GEMINI_MODEL = "gemini-2.5-flash"
TIMEOUT = 300
# Claude models think before answering, and thinking shares the max_tokens
# budget with the visible reply — 4096 is enough for a chat answer but a
# research question can spend it all reasoning and return nothing. This is a
# ceiling, not a charge: unused tokens cost nothing.
ANTHROPIC_MIN_MAX_TOKENS = 8192
# Gemini 2.5+ thinks before answering and the reasoning is billed against
# max_output_tokens, same as Claude. Measured: a 3-part biomedical question
# spent 860 of 900 tokens thinking and returned a 36-token truncated answer.
GEMINI_MIN_MAX_TOKENS = 8192

# Same reasoning-budget problem on the OpenAI-compatible providers: Qwen 3.x
# and the DeepSeek reasoning tiers spend output tokens thinking before any
# visible text, so a low cap yields an empty answer rather than a short one.
# Measured on a five-part biomedical question: Qwen 3.7 Plus spent 6,522
# output tokens and DeepSeek V4 exhausted 8,192 without emitting an answer,
# so the floor has to clear the thinking with room to spare.
OPENAI_LIKE_MIN_MAX_TOKENS = 16384
GEMINI_RETRY_WAIT = 15

# Groq dropped the Llama 3.3 endpoint entirely; this is its current offering.
GROQ_MODEL = "openai/gpt-oss-120b"

# Cohere superseded command-r-plus with the command-a line.
COHERE_MODEL = "command-a-plus-05-2026"

# Models beyond the original seven. Keys are permanent identifiers: they are
# stored in `validations.model` and in cached conversations, so a key must never
# be renamed or repointed at a different model — add a new one instead.
# Every id below was verified against the provider's live /models endpoint.
EXTRA_MODELS = {
    # Anthropic
    "opus55":    {"provider": "anthropic", "id": "claude-opus-5-5"},
    "opus5":     {"provider": "anthropic", "id": "claude-opus-5"},
    "opus48":    {"provider": "anthropic", "id": "claude-opus-4-8"},
    "sonnet55":  {"provider": "anthropic", "id": "claude-sonnet-5-5"},
    "sonnet5":   {"provider": "anthropic", "id": "claude-sonnet-5"},
    "fable51":   {"provider": "anthropic", "id": "claude-fable-5-1"},
    "fable5":    {"provider": "anthropic", "id": "claude-fable-5"},
    # OpenAI — every id here takes max_completion_tokens, not max_tokens.
    "gpt6astra": {"provider": "openai", "id": "gpt-6-astra",  "token_param": "max_completion_tokens"},
    "gpt61sol":  {"provider": "openai", "id": "gpt-6.1-sol",  "token_param": "max_completion_tokens"},
    "gpt56sol":  {"provider": "openai", "id": "gpt-5.6-sol",  "token_param": "max_completion_tokens"},
    "gpt55":     {"provider": "openai", "id": "gpt-5.5",      "token_param": "max_completion_tokens"},
    "gpt54mini": {"provider": "openai", "id": "gpt-5.4-mini", "token_param": "max_completion_tokens"},
    # DeepSeek — the API now lists exactly two models. deepseek-chat (V3) and
    # deepseek-v4-flash are both gone from it, so the old ids are retired.
    "deepseek_pro":   {"provider": "deepseek", "id": "deepseek-v4-pro"},
    # Listed by DeepSeek's API but unresponsive: no reply to a one-line prompt
    # inside the full 300s timeout, repeatedly. Kept registered so historical
    # validations rows resolve, but not offered in the UI.
    "deepseek_flash": {"provider": "deepseek", "id": "deepseek-flash"},
    # Google — Flash tiers only. Gemini Pro returns 429 RESOURCE_EXHAUSTED on a
    # free-tier key, so listing it would ship a model that always errors.
    "gemini38":  {"provider": "google", "id": "gemini-3.8-flash", "search": False},
    "gemini36":  {"provider": "google", "id": "gemini-3.6-flash", "search": False},
    "gemini35":  {"provider": "google", "id": "gemini-3.5-flash", "search": False},
    # Qwen — served through OpenRouter, like the existing "qwen" entry.
    "qwen38flash": {"provider": "openrouter", "id": "qwen/qwen3.8-flash"},
    "qwen38max":   {"provider": "openrouter", "id": "qwen/qwen3.8-max"},
    "qwen37plus":  {"provider": "openrouter", "id": "qwen/qwen3.7-plus"},
    "qwen36flash": {"provider": "openrouter", "id": "qwen/qwen3.6-flash"},
}

# Several models share one provider credential: every Anthropic model
# authenticates with the user's "claude" key, every OpenAI model with "gpt4".
# Without this, a user who supplied an Anthropic key would still be metered on
# Opus 5 because the lookup would miss.
PROVIDER_KEY_FOR_MODEL = {
    k: {"anthropic": "claude", "openai": "gpt4", "deepseek": "deepseek",
        "google": "gemini", "openrouter": "qwen"}[m["provider"]]
    for k, m in EXTRA_MODELS.items()
}

VALID_MODELS    = {"claude", "gpt4", "gemini", "deepseek", "groq", "qwen", "cohere"} | set(EXTRA_MODELS)
# Anthropic and OpenAI models bill per token on the project's keys, so they are
# metered. DeepSeek stays ungated to match the existing "deepseek" entry.
GATED_MODELS    = {"claude", "gpt4"} | {
    k for k, m in EXTRA_MODELS.items() if m["provider"] in ("anthropic", "openai")
}
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

MAX_HISTORY_ITEMS = 12        # server-side cap (≈6 turns) regardless of client
MAX_MODELS_PER_REQUEST = 5    # the site's five boxes (Chat.jsx MAX_BOXES)
MAX_DOC_CONTEXT_CHARS = 80_000  # cap total injected document text

# An item cap alone does not bound cost here. Answers in this app are long
# biomedical writeups with citation lists — several thousand characters each —
# so 12 of them is tens of thousands of tokens, resent to every model on every
# turn and multiplied by the number of boxes. These budgets are what actually
# bounds the bill; the item cap only limits how far back the thread reaches.
MAX_HISTORY_CHARS = 24_000        # ≈6k tokens of history per model per turn
MAX_HISTORY_MSG_CHARS = 4_000     # one long answer cannot crowd out the rest

# Hidden baseline applied to every biomedical-mode request, on top of whatever
# prompt the user selected. Never shown in the UI.
BIOMEDICAL_BASE_PROMPT = (
    "The user is a biomedical researcher, scientist, and graduate student. "
    "Tailor every response for a scientific audience: use precise terminology, "
    "cite mechanisms where relevant, and provide the depth an expert would expect."
    "\n\n"
    # Citations are verified against PubMed after the fact, and a claim with no
    # identifier cannot be checked. Asking for PMIDs is what makes the answer
    # measurable. The negative half matters as much as the positive: told to
    # "always cite", models attach references to definitions and pleasantries,
    # which buries the claims that actually needed support.
    "CITATIONS: When you state a specific experimental finding, a clinical "
    "result, or a claim about what a particular molecule does, give the PMID "
    "in the form `PMID: 12345678` immediately after it. One PMID per claim is "
    "enough.\n"
    "Do NOT attach citations to definitions, background explanation, reasoning "
    "about mechanism, your own recommendations, or conversational replies — "
    "those need no reference and adding one obscures the claims that do.\n"
    "If you do not know a real PMID for a claim, say so plainly, or cite it as "
    "author and year. Never invent, guess at, or approximate an identifier: a "
    "fabricated PMID is far worse than an uncited claim."
)


def _compose_mode_prompt(mode: str, system_prompt: str | None) -> str | None:
    """Prepend the hidden biomedical baseline when in biomedical mode."""
    if mode == "biomedical":
        if system_prompt:
            return f"{BIOMEDICAL_BASE_PROMPT}\n\n{system_prompt}"
        return BIOMEDICAL_BASE_PROMPT
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

    from datetime import date
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
        if user_keys.get("openrouter") and m in OPENROUTER_MODEL_IDS:
            continue
        if not _model_quota_ok(user_id, today, m):
            blocked.add(m)
    return blocked


def _load_biomedical_prompt() -> str:
    path = os.path.join("prompts", "biomedical_prompt.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "You are an expert biomedical researcher. Provide accurate, evidence-based responses."


def _gemini_is_retryable(err: str) -> bool:
    e = err.upper()
    return "503" in err or "UNAVAILABLE" in e or "429" in err or "QUOTA" in e or "RATE" in e or "RESOURCE_EXHAUSTED" in e


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


def _full_document_context(docs: list) -> str:
    """Concatenate whole documents into one framed, capped block (small-doc path)."""
    blocks, total = [], 0
    for d in docs:
        content = (d.get("content") or "").strip()
        if not content:
            continue
        block = f'=== FILE: {d.get("filename", "document")} ===\n{content}\n=== END FILE ==='
        if total + len(block) > MAX_DOC_CONTEXT_CHARS:
            blocks.append("=== [additional document text truncated to conserve tokens] ===")
            break
        blocks.append(block)
        total += len(block)
    if not blocks:
        return ""
    return (
        "The following file(s) were uploaded for this research session. "
        "Treat them as readable reference context for every answer:\n\n"
        + "\n\n".join(blocks)
    )


def _build_full_message(message: str, text_atts: list) -> str:
    """Embed any freshly-attached text files inline in the user message."""
    if not text_atts:
        return message
    file_blocks = []
    for att in text_atts:
        extracted = (att.get("text") or "").strip()
        if extracted:
            file_blocks.append(f'=== BEGIN FILE: {att["filename"]} ===\n{extracted}\n=== END FILE ===')
        else:
            file_blocks.append(
                f'=== FILE: {att["filename"]} ===\n'
                f'(No text could be extracted — the file may be image-based or empty.)\n'
                f'=== END FILE ==='
            )
    joined = "\n\n".join(file_blocks)
    preamble = (
        "The user has provided the following file(s). The full text has been "
        "extracted and is included below — treat it as readable text content.\n\n"
    )
    if message:
        return f"{message}\n\n{preamble}{joined}"
    return f"Please read and analyze the following file(s):\n\n{preamble}{joined}"


def _compose_system(system_prompt: str | None, doc_context: str) -> str | None:
    """
    Compose the system text: the system prompt, then any whole documents.
    Both are identical on every turn, so system + history form a stable prefix
    that OpenAI, DeepSeek and Gemini cache automatically. Per-question RAG
    passages are kept out of here (see _build_document_context).
    """
    parts = [p for p in (system_prompt, doc_context) if p]
    return "\n\n".join(parts) if parts else None


def _trim_msg(content: str, role: str) -> str:
    """Keep a long assistant answer's shape without its bulk.

    The head carries the claims and the tail carries the citation list, which
    is what later turns actually refer back to; the middle is elaboration the
    model can do without. User messages are left alone — they are short, and
    they are the questions the thread is about.
    """
    if role == "user" or len(content) <= MAX_HISTORY_MSG_CHARS:
        return content
    head = int(MAX_HISTORY_MSG_CHARS * 0.7)
    tail = MAX_HISTORY_MSG_CHARS - head
    return f"{content[:head]}\n\n[…trimmed for context…]\n\n{content[-tail:]}"


def _cap_history(history: list) -> list:
    """Most recent turns, under both an item cap and a character budget."""
    valid = [h for h in history
             if h.get("role") in ("user", "assistant") and h.get("content")]

    kept, total = [], 0
    for h in reversed(valid[-MAX_HISTORY_ITEMS:]):
        content = _trim_msg(str(h["content"]), h["role"])
        if kept and total + len(content) > MAX_HISTORY_CHARS:
            break                      # always keep at least the latest exchange
        kept.append({"role": h["role"], "content": content})
        total += len(content)

    kept.reverse()
    # A leading assistant turn is a dangling reply to a question that was
    # dropped; some providers also reject a history that does not start with
    # a user turn.
    while kept and kept[0]["role"] == "assistant":
        kept.pop(0)
    return kept


# ── Content builders for multimodal ────────────────────────────────────────

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
        return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        messages = [{"role": h["role"], "content": h["content"]} for h in _cap_history(history)]
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
        )
        if use_search:
            kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
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
        return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
    except anthropic.RateLimitError:
        return {"model": model_key, "status": "error", "error": "RATE_LIMIT", "usage": _usage()}
    except anthropic.BadRequestError as e:
        msg = str(e)
        if "usage limit" in msg.lower():
            # Surface the reset date the API gives us — this is a configured
            # spend cap, not an empty balance, so the user needs to know when it
            # lifts (or that they can raise it now).
            import re as _re
            when = _re.search(r"regain access on ([0-9]{4}-[0-9]{2}-[0-9]{2}[^\"']*)", msg)
            return {"model": model_key, "status": "error", "error": "USAGE_LIMIT",
                    "limit_resets": when.group(1).strip() if when else None,
                    "usage": _usage()}
        return {"model": model_key, "status": "error", "error": msg[:200], "usage": _usage()}
    except Exception as e:
        return {"model": model_key, "status": "error", "error": str(e)[:200], "usage": _usage()}


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


# Hidden reasoning, not conversation history, is what makes this expensive.
# Measured on one literature question: DeepSeek V4 Pro billed 6,432 output
# tokens to show 1,054 characters — roughly 6,000 tokens of thinking nobody
# ever sees, charged at output rates (3-5x input). Input was 122 tokens.
#
# Disabling it was not a trade of quality for cost. The same question answered
# with thinking off used 722 output tokens and returned 3,134 characters — an
# eighth of the spend and three times the answer, because the reasoning had
# been consuming the budget the answer needed.
#
# Kept configurable per model: a provider that benefits from thinking can opt
# back in here without touching the call sites.
# Left empty deliberately. Disabling reasoning cut DeepSeek's output tokens by
# 89%, but what that measured was cost and answer length — not whether the
# science was still right. Reasoning is exactly what a synthesis question needs,
# and this project asks synthesis questions. Until the two are compared properly
# (same questions, thinking on vs off, scored blind), thinking stays on and the
# saving is pursued through caching instead.
#
# Populate a provider here to disable its reasoning, e.g.
#   "deepseek": {"thinking": {"type": "disabled"}}
REASONING_OFF: dict = {}

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
        return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
    try:
        client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url) if base_url \
            else openai.AsyncOpenAI(api_key=api_key)
        messages = []
        composed = _compose_system(system_prompt, doc_context)
        if composed:
            messages.append({"role": "system", "content": composed})
        for h in _cap_history(history):
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
        reasoning = REASONING_OFF.get(provider or "")
        extra_body = reasoning or REASONING_CAP.get(provider or "")
        extra = {"extra_body": extra_body} if extra_body else {}
        # With thinking off the floor is no longer needed — it existed only to
        # leave room for reasoning that no longer happens.
        budget = max_tokens if reasoning else max(max_tokens, OPENAI_LIKE_MIN_MAX_TOKENS)

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
                return {"model": model_key, "status": "error", "error": "RATE_LIMIT", "usage": _usage()}
            return {"model": model_key, "status": "error", "error": "MODEL_UNAVAILABLE", "usage": _usage()}
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
        return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
    except openai.RateLimitError:
        return {"model": model_key, "status": "error", "error": "RATE_LIMIT", "usage": _usage()}
    except Exception as e:
        err = str(e)
        if "402" in err or "requires more credits" in err:
            return {"model": model_key, "status": "error",
                    "error": "INSUFFICIENT_CREDITS", "usage": _usage()}
        low = err.lower()
        # OpenRouter/provider errors arrive as a big JSON string — map to clean codes
        if "429" in err or "rate-limit" in low or "rate limit" in low:
            return {"model": model_key, "status": "error", "error": "RATE_LIMIT", "usage": _usage()}
        if "unauthorized" in low or "401" in err or "no auth" in low:
            return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
        if "does not support" in low or "no endpoints" in low or "not support" in low:
            return {"model": model_key, "status": "error", "error": "MODEL_UNAVAILABLE", "usage": _usage()}
        return {"model": model_key, "status": "error", "error": err[:200], "usage": _usage()}


async def _call_gpt(message, system_prompt, doc_context, history, image_atts, max_tokens=4096, user_api_key=None):
    api_key = user_api_key or os.getenv("OPENAI_API_KEY")
    return await _call_openai_like("gpt4", "gpt-4o-search-preview", None, api_key,
                                   message, system_prompt, doc_context, history, image_atts, max_tokens)


async def _call_gemini(message, system_prompt, doc_context, history, image_atts, max_tokens=4096,
                       user_api_key=None, model_id=None, model_key="gemini", use_search=False):
    api_key = user_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
    try:
        client = genai.Client(api_key=api_key)
        contents = []
        for h in _cap_history(history):
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
            return {"model": model_key, "status": "error", "error": "RATE_LIMIT", "usage": _usage()}
        if "API_KEY" in err.upper() or "INVALID" in err.upper():
            return {"model": model_key, "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
        return {"model": model_key, "status": "error", "error": err, "usage": _usage()}


async def _call_cohere(message, system_prompt, doc_context, history, image_atts, max_tokens=4096, user_api_key=None):
    api_key = user_api_key or os.getenv("COHERE_API_KEY")
    if not api_key:
        return {"model": "cohere", "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
    try:
        client = cohere.AsyncClientV2(api_key=api_key)
        messages = []
        composed = _compose_system(system_prompt, doc_context)
        if composed:
            messages.append({"role": "system", "content": composed})
        for h in _cap_history(history):
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        response = await asyncio.wait_for(
            client.chat(model=COHERE_MODEL, messages=messages,
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
            return {"model": "cohere", "status": "error",
                    "error": "EMPTY_RESPONSE", "usage": usage}
        return {"model": "cohere", "status": "ok", "text": text, "usage": usage}
    except asyncio.TimeoutError:
        return {"model": "cohere", "status": "TIMED_OUT", "error": "TIMEOUT", "usage": _usage()}
    except Exception as e:
        err = str(e)
        if "401" in err or "unauthorized" in err.lower():
            return {"model": "cohere", "status": "error", "error": "API_KEY_INVALID", "usage": _usage()}
        if "429" in err or "rate" in err.lower():
            return {"model": "cohere", "status": "error", "error": "RATE_LIMIT", "usage": _usage()}
        return {"model": "cohere", "status": "error", "error": err, "usage": _usage()}




OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL_IDS = {
    "claude":   "anthropic/claude-sonnet-4",
    "gpt4":     "openai/gpt-4o",
    "qwen":     "qwen/qwen-2.5-72b-instruct",
    "deepseek": "deepseek/deepseek-chat",
}


async def _run_model(model, message, system_prompt, doc_context, history, image_atts, max_tokens, user_keys=None, web_search=False):
    """Run a single model. user_keys is a dict of provider->api_key from the frontend."""
    user_keys = user_keys or {}
    openrouter_key = user_keys.get("openrouter")

    if model == "claude":
        if user_keys.get("claude"):
            return await _call_claude(message, system_prompt, doc_context, history, image_atts, max_tokens, user_api_key=user_keys["claude"], use_search=web_search)
        if openrouter_key:
            return await _call_openai_like("claude", OPENROUTER_MODEL_IDS["claude"], OPENROUTER_URL,
                                           openrouter_key, message, system_prompt, doc_context, history, [], max_tokens)
        return await _call_claude(message, system_prompt, doc_context, history, image_atts, max_tokens, use_search=web_search)

    if model == "gpt4":
        if user_keys.get("gpt4"):
            return await _call_gpt(message, system_prompt, doc_context, history, image_atts, max_tokens, user_api_key=user_keys["gpt4"])
        if openrouter_key:
            return await _call_openai_like("gpt4", OPENROUTER_MODEL_IDS["gpt4"], OPENROUTER_URL,
                                           openrouter_key, message, system_prompt, doc_context, history, [], max_tokens)
        return await _call_gpt(message, system_prompt, doc_context, history, image_atts, max_tokens)

    if model == "gemini":
        return await _call_gemini(message, system_prompt, doc_context, history, image_atts, max_tokens,
                                  user_api_key=user_keys.get("gemini"), use_search=web_search)

    if model == "qwen":
        # Qwen only runs via OpenRouter. Prefer the user's own OpenRouter key for higher limits.
        key = user_keys.get("qwen") or openrouter_key or os.getenv("OPENROUTER_API_KEY")
        return await _call_openai_like("qwen", OPENROUTER_MODEL_IDS["qwen"], OPENROUTER_URL,
                                       key, message, system_prompt, doc_context, history, [], max_tokens,
                                       provider="openrouter")

    if model == "deepseek":
        key = user_keys.get("deepseek") or os.getenv("DEEPSEEK_API_KEY")
        return await _call_openai_like("deepseek", "deepseek-chat", "https://api.deepseek.com",
                                       key, message, system_prompt, doc_context, history, [], max_tokens,
                                       provider="deepseek")

    if model == "groq":
        key = user_keys.get("groq") or os.getenv("GROQ_API_KEY")
        # Groq retired llama-3.3-70b-versatile; the id now 404s. The key stays
        # "groq" because it is written into validations.model, but note that it
        # no longer denotes the same model — rows recorded before 2026-10-01
        # are Llama 3.3 and should be analysed separately from later ones.
        return await _call_openai_like("groq", GROQ_MODEL, "https://api.groq.com/openai/v1",
                                       key, message, system_prompt, doc_context, history, [], max_tokens)

    if model == "cohere":
        key = user_keys.get("cohere") or os.getenv("COHERE_API_KEY")
        return await _call_cohere(message, system_prompt, doc_context, history, image_atts, max_tokens, user_api_key=key)

    spec = EXTRA_MODELS.get(model)
    if spec:
        provider = spec["provider"]
        if provider == "anthropic":
            return await _call_claude(message, system_prompt, doc_context, history, image_atts,
                                      max_tokens, user_api_key=user_keys.get("claude"),
                                      model_id=spec["id"], model_key=model, use_search=web_search)
        if provider == "openai":
            key = user_keys.get("gpt4") or os.getenv("OPENAI_API_KEY")
            return await _call_openai_like(model, spec["id"], None, key,
                                           message, system_prompt, doc_context, history, image_atts, max_tokens,
                                           token_param=spec.get("token_param", "max_tokens"))
        if provider == "deepseek":
            key = user_keys.get("deepseek") or os.getenv("DEEPSEEK_API_KEY")
            return await _call_openai_like(model, spec["id"], "https://api.deepseek.com", key,
                                           message, system_prompt, doc_context, history, [], max_tokens,
                                           provider="deepseek")
        if provider == "google":
            return await _call_gemini(message, system_prompt, doc_context, history, image_atts, max_tokens,
                                      user_api_key=user_keys.get("gemini"),
                                      model_id=spec["id"], model_key=model,
                                      use_search=web_search and spec.get("search", True))
        if provider == "openrouter":
            key = user_keys.get("qwen") or openrouter_key or os.getenv("OPENROUTER_API_KEY")
            return await _call_openai_like(model, spec["id"], OPENROUTER_URL, key,
                                           message, system_prompt, doc_context, history, [], max_tokens,
                                           provider="openrouter")

    return {"model": model, "status": "error", "error": "UNKNOWN_MODEL", "usage": _usage()}


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
                   user_keys=None, gated_models=None, web_search=False):
    """
    Generator of SSE frames. A background thread runs all model calls
    concurrently in its own event loop and pushes each result onto a
    thread-safe queue the moment it completes; this generator yields a frame
    per result so the client renders each panel as soon as it's ready.

    gated_models: subset of `models` (Claude/GPT-4o) that need a per-model daily
    quota check. That check is a DB call, so it runs in a thread-pool executor —
    concurrently with the free models, which start immediately and never wait on it.
    A gated model over its limit emits DAILY_LIMIT_REACHED without calling any provider.
    """
    gated_set = set(gated_models or [])
    q: "queue.Queue" = queue.Queue()
    results: dict = {}

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_one(m):
            # Gated models: run the quota check off the event loop so it doesn't
            # stall the free models that are streaming concurrently.
            if m in gated_set:
                blocked = await loop.run_in_executor(
                    None, _gated_blocked_models, user, user_keys, [m]
                )
                if blocked:
                    res = {"model": m, "status": "error", "error": "DAILY_LIMIT_REACHED", "usage": _usage()}
                    results[m] = res
                    q.put(("model", m, res))
                    return
            try:
                res = await _run_model(m, full_message, system_prompt, doc_context,
                                       histories.get(m, history), image_atts, max_tokens, user_keys,
                                       web_search=web_search)
            except Exception as e:
                res = {"model": m, "status": "error", "error": str(e), "usage": _usage()}
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

    models = list(dict.fromkeys(m for m in models if m in VALID_MODELS))
    if not models:
        return jsonify({"error": "NO_VALID_MODELS"}), 400
    if len(models) > MAX_MODELS_PER_REQUEST:
        return jsonify({"error": "TOO_MANY_MODELS",
                        "message": f"At most {MAX_MODELS_PER_REQUEST} models per question"}), 400
    if not message and not attachments:
        return jsonify({"error": "MISSING_MESSAGE"}), 400

    # Claude and GPT-4o each have their OWN 5/day limit. The per-model quota
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
                           user_keys, gated_requested, web_search=web_search),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming path: compute blocked set up-front (rarely used by the UI).
    blocked_models = _gated_blocked_models(user, user_keys, gated_requested)
    run_models = [m for m in models if m not in blocked_models]

    async def run():
        tasks = [_run_model(m, full_message, system_prompt, doc_context,
                            histories.get(m, history), image_atts, max_tokens, user_keys,
                            web_search=web_search)
                 for m in run_models]
        done = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for m, res in zip(run_models, done):
            if isinstance(res, Exception):
                out[m] = {"model": m, "status": "error", "error": str(res), "usage": _usage()}
            else:
                out[m] = res
        # Emit a limit error for each blocked gated model
        for m in blocked_models:
            out[m] = {"model": m, "status": "error", "error": "DAILY_LIMIT_REACHED", "usage": _usage()}
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
