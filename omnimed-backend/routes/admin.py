import os
import asyncio
import anthropic
from flask import Blueprint, request, jsonify, g
from routes.auth import require_auth
from services import supabase_service, stats_service

admin_bp = Blueprint("admin", __name__)


def _admin_emails() -> list[str]:
    """Admin emails, comma-separated. Supports ADMIN_EMAILS (list) or the legacy
    single ADMIN_EMAIL. Lower-cased for case-insensitive comparison."""
    raw = os.getenv("ADMIN_EMAILS") or os.getenv("ADMIN_EMAIL", "chitrashenshah@gmail.com")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def _require_admin():
    user = getattr(g, "user", None)
    email = (user or {}).get("email", "") or ""
    if email.lower() not in _admin_emails():
        return jsonify({"error": "FORBIDDEN"}), 403
    return None


@admin_bp.route("/admin/granted-users", methods=["GET"])
@require_auth
def get_granted_users():
    err = _require_admin()
    if err: return err
    return jsonify({"users": supabase_service.get_granted_users()}), 200


@admin_bp.route("/admin/granted-users", methods=["POST"])
@require_auth
def grant_user():
    err = _require_admin()
    if err: return err
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "").strip()
    email   = data.get("email", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    row = supabase_service.grant_user_access(user_id, email)
    return jsonify({"user": row}), 201


@admin_bp.route("/admin/granted-users/<user_id>", methods=["DELETE"])
@require_auth
def revoke_user(user_id):
    err = _require_admin()
    if err: return err
    supabase_service.revoke_user_access(user_id)
    # Clear from in-memory cache too
    try:
        from routes.chat import _quota_cache
        _quota_cache.pop(user_id, None)
    except Exception:
        pass
    return jsonify({"status": "revoked"}), 200


# ── Research Report (publication-ready wet-lab validation summary) ──────────

@admin_bp.route("/admin/research-report", methods=["POST"])
@require_auth
def research_report():
    """
    Aggregate all wet-lab validation scores into a publication-ready report:
    descriptive + inferential statistics, and (optionally) an AI-generated
    Results-section narrative that also mines the reviewer notes for themes.
    """
    err = _require_admin()
    if err:
        return err

    want_narrative = (request.get_json(silent=True) or {}).get("narrative", False)

    rows = supabase_service.get_all_validations()
    stats = stats_service.compute_model_stats(rows)

    narrative = None
    narrative_error = None
    if want_narrative and stats["total_validations"] > 0:
        try:
            result = asyncio.run(_generate_narrative(stats, rows))
            if result.get("status") == "ok":
                narrative = result.get("raw_response")
            else:
                narrative_error = result.get("error", "NARRATIVE_FAILED")
        except Exception as e:
            narrative_error = str(e)[:150]

    # Strip raw score arrays from the payload's descriptive rows? Keep them —
    # the frontend uses them for distribution plots. They're small.
    return jsonify({
        "stats": stats,
        "narrative": narrative,
        "narrative_error": narrative_error,
    }), 200


async def _generate_narrative(stats: dict, rows: list):
    """Build a prompt from the computed stats + reviewer notes and ask Gemini for
    a formal Results-section write-up plus a thematic analysis of the notes."""
    # Compact stats table for the prompt
    lines = []
    for d in stats["descriptive"]:
        ci = (f", 95% CI [{d['ci95_low']}, {d['ci95_high']}]"
              if d["ci95_low"] is not None else "")
        lines.append(
            f"- {d['label']}: N={d['n']}, M={d['mean']}, SD={d['sd']}, "
            f"SEM={d['sem']}, Mdn={d['median']}, range {d['min']}-{d['max']}{ci}"
        )
    stats_block = "\n".join(lines)

    anova = stats.get("anova")
    anova_block = "Not computed (insufficient data)." if not anova else (
        f"One-way ANOVA: F={anova['f']}, p={anova['p']} "
        f"({'significant' if anova['significant'] else 'not significant'} at α=0.05)."
    )
    sig_pairs = [p for p in stats.get("pairwise", []) if p["significant"]]
    pairwise_block = "None reached Bonferroni-corrected significance." if not sig_pairs else "\n".join(
        f"- {p['model_a']} vs {p['model_b']}: Δ={p['mean_diff']}, t={p['t']}, p={p['p']}"
        for p in sig_pairs
    )

    # A sample of reviewer notes for thematic analysis (cap to keep prompt lean)
    notes = [r.get("researcher_notes", "").strip() for r in rows if r.get("researcher_notes")]
    notes = [n for n in notes if n][:60]
    notes_block = "\n".join(f'- "{n}"' for n in notes) if notes else "(No free-text reviewer notes were recorded.)"

    power_note = ""
    if stats.get("underpowered"):
        power_note = (
            " Sample sizes are small (some groups below "
            f"{stats['min_n_for_power']} validations) — frame findings as preliminary/pilot "
            "and do not overclaim significance."
        )

    system_prompt = (
        "You are a scientific writing assistant for a peer-reviewed biomedical paper. "
        "The study (OmniMed) queries seven LLMs to identify therapeutic microRNA "
        "candidates for cardiovascular disease; researchers score each model's response "
        "1-10 against wet-lab outcomes to build a reliability profile.\n\n"
        "Write TWO short sections, CONCISE and PRECISE — this goes into a manuscript, "
        "so no filler, no hedging, no generic AI phrasing:\n\n"
        "## Results\n"
        "One tight paragraph (120-180 words). Report exact stats in standard notation "
        "(M, SD, SEM, 95% CI, N; ANOVA F, p; t, p for significant contrasts only). "
        "State which model ranked highest and whether differences are significant. "
        "Use only the numbers provided — never invent values.\n\n"
        "## Reviewer-Note Themes\n"
        "3-5 bullet points, each a specific recurring pattern from the notes (e.g. a "
        "model over-/under-predicting a specific effect). If notes are sparse, say so in "
        "one line rather than padding.\n\n"
        "Be factual and specific. Ground every statement in the data given." + power_note
    )

    user_message = (
        f"PER-MODEL RELIABILITY STATISTICS (1-10 scale, higher = more accurate vs wet-lab):\n"
        f"{stats_block}\n\n"
        f"Total validations: {stats['total_validations']} across {stats['n_models']} models.\n\n"
        f"OVERALL TEST:\n{anova_block}\n\n"
        f"SIGNIFICANT PAIRWISE DIFFERENCES (Bonferroni-corrected):\n{pairwise_block}\n\n"
        f"REVIEWER NOTES:\n{notes_block}"
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"status": "error", "error": "ANTHROPIC_API_KEY not set on the server"}
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,   # keeps the write-up tight, not sprawling
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return {"status": "ok", "raw_response": text}
    except anthropic.AuthenticationError:
        return {"status": "error", "error": "Anthropic API key invalid"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:150]}
