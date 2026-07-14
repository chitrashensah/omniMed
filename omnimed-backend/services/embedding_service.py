# Embeddings via OpenAI text-embedding-3-small (1536 dims, ~$0.02 / 1M tokens).
# Used by the RAG pipeline to embed document chunks (once, at upload) and user
# queries (per question). All calls are best-effort — callers must handle None.

import os
import openai

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI | None:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        _client = openai.OpenAI(api_key=key)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of texts. Returns list of vectors, or None on any failure."""
    if not texts:
        return []
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
        return [d.embedding for d in resp.data]
    except Exception:
        return None


def embed_query(text: str) -> list[float] | None:
    """Embed a single query string. Returns one vector, or None on failure."""
    out = embed_texts([text])
    return out[0] if out else None
