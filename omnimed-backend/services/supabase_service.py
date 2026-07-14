# OmniMed — Supabase data-access layer.
#
# Handles everything the Flask backend persists or reads server-side:
#   • token verification (validating the user's Supabase JWT)
#   • documents (per-session uploaded file text, injected into prompts)
#   • usage logging (per-model token accounting)
#   • session turn appends (atomic, via RPC)
#   • per-model daily quota for gated models (Claude / GPT-4o)
#   • admin-granted "unlimited access" users
#
# All table definitions live in supabase_setup.sql — run that once in the
# Supabase SQL editor. The data client prefers the service-role key so backend
# writes bypass RLS; the auth client uses the anon key only to verify tokens.

import os
from supabase import create_client, Client

_client: Client | None = None       # data client (service role preferred — bypasses RLS)
_auth_client: Client | None = None   # anon client used only to verify user JWTs


def _get_client() -> Client:
    """
    Data client for server-side writes/reads. Prefers the service-role key
    (SUPABASE_SERVICE_KEY) so backend writes — usage logs, documents, atomic
    session appends — bypass RLS. Falls back to the anon key (SUPABASE_KEY),
    in which case RLS-protected writes will fail unless run as the user.
    """
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and a key (SUPABASE_SERVICE_KEY or SUPABASE_KEY) must be set in .env")
        _client = create_client(url, key)
    return _client


def _get_auth_client() -> Client:
    """Anon client used only to validate user access tokens."""
    global _auth_client
    if _auth_client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        _auth_client = create_client(url, key)
    return _auth_client


def verify_token(access_token: str) -> dict | None:
    """
    Validate a Supabase access token. Returns {"id", "email"} on success,
    or None if the token is missing/invalid/expired.
    """
    if not access_token:
        return None
    try:
        resp = _get_auth_client().auth.get_user(access_token)
        user = getattr(resp, "user", None)
        if not user:
            return None
        return {"id": user.id, "email": getattr(user, "email", None)}
    except Exception:
        return None


# ── Documents (per-session file text, injected once + cached) ──────────────

def save_document(session_id: str, user_id: str | None, filename: str,
                  file_type: str, content: str) -> dict | None:
    """Persist extracted file text for a session. Skips empty content."""
    if not content or not content.strip():
        return None
    client = _get_client()
    result = client.table("documents").insert({
        "session_id": session_id,
        "user_id": user_id,
        "filename": filename,
        "file_type": file_type,
        "content": content,
    }).execute()
    return result.data[0] if result.data else None


def get_documents(session_id: str) -> list:
    """Return all stored documents for a session, oldest first."""
    if not session_id:
        return []
    client = _get_client()
    result = (
        client.table("documents")
        .select("filename, file_type, content, created_at")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []


# ── Usage logging (token accounting) ───────────────────────────────────────

def log_usage(rows: list[dict]) -> None:
    """Bulk-insert usage rows. Best-effort: never raises into the request path."""
    if not rows:
        return
    try:
        _get_client().table("usage_logs").insert(rows).execute()
    except Exception:
        pass


# ── Atomic session-turn append (via SQL RPC) ───────────────────────────────

def append_turn(session_id: str, role: str, content: str) -> None:
    """
    Atomically append a turn via the append_session_turn RPC. Only works for
    uuid session ids; silently no-ops for frontend string ids or on failure.
    """
    try:
        _get_client().rpc("append_session_turn", {
            "p_session_id": session_id,
            "p_turn": {"role": role, "content": content},
        }).execute()
    except Exception:
        pass


# ── User Quota (per-model daily free limit for Claude/GPT-4o) ──────────────

def check_and_increment_user_quota(user_id: str, today: str, model: str) -> int:
    """
    Atomically increment the user's daily count for a specific gated model
    (Claude and GPT-4o are tracked separately). Returns the new count.
    """
    result = _get_client().rpc(
        "increment_user_quota",
        {"p_user_id": user_id, "p_date": today, "p_model": model},
    ).execute()
    return result.data if isinstance(result.data, int) else 0


def is_user_granted(user_id: str) -> bool:
    """Returns True if the user has been granted premium access by admin."""
    result = _get_client().table("granted_users") \
        .select("id") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    return len(result.data) > 0


# ── Granted Users (admin-managed) ─────────────────────────

def get_granted_users() -> list:
    result = _get_client().table("granted_users") \
        .select("id, user_id, email, granted_at") \
        .order("granted_at", desc=False) \
        .execute()
    return result.data or []


def grant_user_access(user_id: str, email: str) -> dict:
    result = _get_client().table("granted_users").upsert({
        "user_id": user_id,
        "email":   email,
    }, on_conflict="user_id").execute()
    return result.data[0]


def revoke_user_access(user_id: str) -> None:
    _get_client().table("granted_users") \
        .delete() \
        .eq("user_id", user_id) \
        .execute()


# ── RAG: document chunks + vector retrieval ────────────────────────────────

def save_document_chunks(session_id: str, user_id: str | None,
                         chunks: list[str], embeddings: list[list[float]]) -> None:
    """Bulk-insert embedded chunks for a session. Best-effort; never raises."""
    if not chunks or not embeddings or len(chunks) != len(embeddings):
        return
    rows = [
        {
            "session_id": session_id,
            "user_id": user_id,
            "chunk_index": i,
            "content": chunk,
            "embedding": emb,
        }
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
    ]
    try:
        _get_client().table("document_chunks").insert(rows).execute()
    except Exception:
        pass


def match_document_chunks(session_id: str, query_embedding: list[float],
                          k: int = 6) -> list[str]:
    """Return the top-k most relevant chunk texts for a query via pgvector. []"""
    try:
        r = _get_client().rpc("match_document_chunks", {
            "p_session_id": session_id,
            "p_query_embedding": query_embedding,
            "p_match_count": k,
        }).execute()
        return [row["content"] for row in (r.data or []) if row.get("content")]
    except Exception:
        return []


# ── Research report: all wet-lab validations (admin) ───────────────────────

def get_all_validations() -> list:
    """All scored validations for the research report (admin, service role)."""
    try:
        r = _get_client().table("validations") \
            .select("model, verdict, researcher_notes, submitted_by, created_at") \
            .not_.is_("verdict", "null") \
            .order("created_at", desc=False) \
            .execute()
        return r.data or []
    except Exception:
        return []
