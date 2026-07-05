# /meta-analysis — "Get Conclusion" endpoint. Sends every model's answer to
# Gemini and returns one unified, structured comparison of them.

import asyncio
from flask import Blueprint, request, jsonify
from services.gemini_meta import call_gemini_meta
from routes.auth import require_auth

compare_bp = Blueprint("compare", __name__)

def build_synthesis_prompt(model_labels: dict) -> str:
    names = list(model_labels.values())
    count = len(names)
    names_str = ", ".join(names[:-1]) + f" and {names[-1]}" if count > 1 else names[0]
    per_model = "\n\n".join(
        f"**{label}:** Summarise key points in 2–4 sentences. Quote or paraphrase the most important specific claims."
        for label in names
    )
    agreement_threshold = "all models" if count > 2 else "both models"

    return f"""\
You are a neutral AI response analyst. {count} AI model{"s" if count > 1 else ""} — {names_str} — each answered the same question independently. Analyze and compare their responses using this exact structure:

## What each model said

{per_model}

## Where they agree
List the specific facts, concepts, or conclusions {agreement_threshold} share. Be concrete — name the shared terms, findings, or recommendations.

## Where they differ
Identify any contradictions, omissions, or different emphasis between models. If one model mentions something the others don't, call it out explicitly.

## Overall takeaway
One concise paragraph: the most reliable answer given the consensus, and which model added the most unique value. If only one model responded, summarise its key answer directly.

Output plain text with markdown headings as shown above. Do not add extra sections.\
"""


def run_comparison_logic():
    """Shared logic for /compare and /meta-analysis."""
    data = request.get_json(silent=True) or {}

    session_id      = data.get("session_id")
    model_responses = data.get("model_responses")
    model_labels    = data.get("model_labels", {})

    if not model_responses or not isinstance(model_responses, dict):
        return jsonify({"error": "MISSING_MODEL_RESPONSES",
                        "message": "Provide model_responses dict"}), 400

    # Filter to only models that have actual responses
    valid = {k: v for k, v in model_responses.items() if v and v != '[no response]'}
    if not valid:
        return jsonify({"error": "NO_VALID_RESPONSES",
                        "message": "No models returned a valid response"}), 400

    # Build model labels for prompt — fallback to key if label not provided
    labels = {k: model_labels.get(k, k) for k in valid}

    sections = "\n\n".join(
        f"<model id=\"{labels[k]}\">\n{v}\n</model>"
        for k, v in valid.items()
    )

    synthesis_prompt = build_synthesis_prompt(labels)
    user_message = f"<responses>\n{sections}\n</responses>"
    result = asyncio.run(call_gemini_meta(user_message, None, system_prompt_override=synthesis_prompt))

    if result.get("status") == "TIMED_OUT":
        return jsonify({"error": "TIMEOUT", "model": "gemini_meta",
                        "message": result.get("message")}), 504

    if result.get("status") == "error":
        return jsonify({"error": result.get("error", "META_FAILED"),
                        "model": "gemini_meta"}), 502

    return jsonify({
        "status": "ok",
        "session_id": session_id,
        "meta_analysis": result.get("raw_response") or result.get("parsed_json"),
    }), 200


@compare_bp.route("/meta-analysis", methods=["POST", "OPTIONS"])
@require_auth
def meta_analysis():
    return run_comparison_logic()
