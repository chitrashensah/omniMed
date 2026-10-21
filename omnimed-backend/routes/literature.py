# Literature search and citation verification endpoints.
#
# These back Layer 1 of the validation pipeline: objective, deterministic
# checks that need no AI judgement and no wet-lab work. Keeping them on their
# own routes (rather than inside /ask) means a citation can be re-verified
# later, independently of the conversation that produced it.

import asyncio
from flask import Blueprint, request, jsonify

from routes.auth import require_auth
from services import literature_service as lit

literature_bp = Blueprint("literature", __name__)

MAX_LIMIT = 25
# A long answer is ~20k characters. The cap stops one request from pasting a
# whole corpus in and turning it into thousands of PubMed/Crossref lookups.
MAX_TEXT_CHARS = 100_000


def _too_long(text: str):
    if len(text) > MAX_TEXT_CHARS:
        return jsonify({"error": "TEXT_TOO_LONG",
                        "message": f"At most {MAX_TEXT_CHARS:,} characters per check"}), 413
    return None


@literature_bp.route("/literature/search", methods=["POST", "OPTIONS"])
@require_auth
def literature_search():
    """Search the biomedical databases and return one merged result set."""
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "MISSING_QUERY",
                        "message": "Provide a 'query' string"}), 400

    sources = data.get("sources") or list(lit.SOURCES)
    sources = tuple(s for s in sources if s in lit.SOURCES)
    if not sources:
        return jsonify({"error": "NO_VALID_SOURCES",
                        "message": f"Valid sources: {', '.join(lit.SOURCES)}"}), 400

    try:
        limit = max(1, min(int(data.get("limit", 8)), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 8

    def _year(v):
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    try:
        result = asyncio.run(lit.search(
            query, sources=sources, limit=limit,
            year_from=_year(data.get("year_from")),
            year_to=_year(data.get("year_to")),
        ))
    except Exception as e:
        return jsonify({"error": "SEARCH_FAILED", "message": str(e)[:200]}), 502

    return jsonify({"status": "ok", **result}), 200


@literature_bp.route("/literature/unsourced-claims", methods=["POST", "OPTIONS"])
@require_auth
def unsourced_claims():
    """Look for literature behind the claims an answer made without citing.

    Deliberately separate from citation verification, and never folded into the
    hallucination or miscitation rates. Verifying citations only measures the
    claims a model chose to cite, which rewards citing less; this asks whether
    the uncited ones have anything behind them at all.

    A search cannot establish that a claim is true. Finding nothing is the
    informative result — it marks a claim for a human to look at.
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text") or ""
    if not text.strip():
        return jsonify({"error": "MISSING_TEXT",
                        "message": "Provide a 'text' string to scan"}), 400
    if (err := _too_long(text)):
        return err

    try:
        result = asyncio.run(lit.check_unsourced_claims(text))
    except Exception as e:
        return jsonify({"error": "CHECK_FAILED", "message": str(e)[:200]}), 502

    return jsonify({"status": "ok", **result}), 200


@literature_bp.route("/literature/verify-citations", methods=["POST", "OPTIONS"])
@require_auth
def verify_citations_route():
    """Check every PMID and DOI in a block of text against PubMed and Crossref.

    Returns per-citation results plus a hallucination rate. This is the
    objective per-model accuracy signal — it is computed from public
    registries, not from model judgement.
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text") or ""
    if not text.strip():
        return jsonify({"error": "MISSING_TEXT",
                        "message": "Provide a 'text' string to scan"}), 400
    if (err := _too_long(text)):
        return err

    try:
        result = asyncio.run(lit.verify_citations(text))
    except Exception as e:
        return jsonify({"error": "VERIFY_FAILED", "message": str(e)[:200]}), 502

    return jsonify({"status": "ok", **result}), 200
