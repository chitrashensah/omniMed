# RAG helpers: chunk documents for embedding, and decide when a session's
# documents are small enough to send whole vs. large enough to retrieve from.

import re

# Below this total character count, skip RAG and inject the full document —
# retrieval overhead isn't worth it and small papers fit comfortably in context.
RAG_THRESHOLD_CHARS = 12_000     # ~3k tokens

CHUNK_SIZE = 1_100               # ~275 tokens per chunk
CHUNK_OVERLAP = 150              # carry-over so ideas spanning a boundary aren't lost
TOP_K = 6                        # chunks retrieved per query


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping chunks, preferring paragraph/sentence boundaries
    so chunks stay semantically coherent. Returns a list of chunk strings.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    # Split into paragraphs first, then pack paragraphs into chunks.
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf = ""

    for para in paras:
        para = para.strip()
        if not para:
            continue
        # A single oversized paragraph → hard-split it on sentence boundaries.
        if len(para) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long(para, chunk_size, overlap))
            continue
        if len(buf) + len(para) + 2 <= chunk_size:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            chunks.append(buf)
            # start next buffer with a tail overlap of the previous chunk
            tail = buf[-overlap:] if overlap else ""
            buf = f"{tail}\n\n{para}" if tail else para

    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def _split_long(para: str, chunk_size: int, overlap: int) -> list[str]:
    """Hard-split an over-long paragraph on sentence boundaries, then on raw
    character count for any sentence still longer than chunk_size."""
    sentences = re.split(r"(?<=[.!?])\s+", para)
    out: list[str] = []
    buf = ""
    for s in sentences:
        # A single sentence longer than a chunk → slice it by characters.
        if len(s) > chunk_size:
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(s), chunk_size - overlap):
                out.append(s[i:i + chunk_size])
            continue
        if len(buf) + len(s) + 1 <= chunk_size:
            buf = f"{buf} {s}" if buf else s
        else:
            if buf:
                out.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = f"{tail} {s}" if tail else s
    if buf:
        out.append(buf)
    return out
