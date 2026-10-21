# /agent/score — Layer 2 automated scoring.
#
# Runs Layer 1 (deterministic citation verification) first, then hands the
# answer plus those findings to a judge model. Both results come back, in
# separate fields, so the caller can store them in separate columns. They are
# never combined into one number here — see services/agent_service.py.
#
# The judge is a different model from the one being scored wherever possible.
# A model scoring its own answer is not a measurement, and the backend enforces
# that rather than trusting the client to pick a fair judge.

import time
import asyncio
from datetime import date
from flask import Blueprint, request, jsonify

from routes.auth import require_auth, current_user
from routes.chat import (_run_model, _is_granted_cached, VALID_MODELS, GATED_MODELS,
                         PROVIDER_KEY_FOR_MODEL, ADMIN_EMAILS)
from services import agent_service, literature_service as lit

agent_bp = Blueprint("agent", __name__)

# Cheap, capable, and not in the Anthropic family that most scored answers come
# from — a judge from a different lineage is less likely to share the scored
# model's blind spots. Ordered by preference; the first usable one wins.
# Ordered by measured latency on a scoring-sized prompt, not by capability.
# Judging a 600-token prompt is an easy task that every model here can do; what
# separates them is whether they answer promptly. Measured 2026-10-01:
# gpt-5.4-mini 2.8s, gemini 2.5 31s, gemini 3.5 and deepseek-flash both >90s.
JUDGE_PREFERENCE = ("gemini36", "gemini", "gpt54mini", "gemini35",
                    "deepseek", "deepseek_flash", "qwen36flash", "groq")

JUDGE_MAX_TOKENS = 700          # a score object and two sentences, nothing more

# A model call may wait up to TIMEOUT (300s) in chat.py, which is right for a
# research answer and far too long for a judge. Scoring a short prompt either
# happens quickly or is not going to: two judges in the chain sat past 90s and
# blocked the whole batch behind them.
JUDGE_TIMEOUT = 45


# One request scores the answers of one turn. The site shows at most five
# boxes; the margin covers pasted or host answers. Without a cap, one request
# could carry thousands of "answers", each a judge call on the project's keys.
MAX_ANSWERS = 8


def _judge_affordable(key: str, user_keys: dict) -> bool:
    """May this model judge for the current caller?

    Metered models (Claude, GPT) cost the project per token, and /ask allows
    them only within the 5/day limit. Without this check the judge endpoints
    were a way around that limit: the client chooses the judge, so any user
    could run Claude Opus or GPT on the project's keys without limit. A metered
    judge is used only with the caller's own key for its provider, or for an
    admin or granted user. No quota is spent here: scoring is not a chat turn.
    """
    if key not in GATED_MODELS:
        return True
    if user_keys.get(PROVIDER_KEY_FOR_MODEL.get(key, key)):
        return True
    user = current_user() or {}
    if (user.get("email") or "").lower() in ADMIN_EMAILS:
        return True
    return bool(user.get("id")) and _is_granted_cached(user["id"], date.today().isoformat())


def _judge_chain(scored_model: str, requested: str | None, user_keys: dict | None = None) -> list:
    """Judges to try, in order, none of them the model under test.

    A chain rather than a single pick: the cheap judges are exactly the ones
    on free tiers that return 503 under load, and a scoring run that dies
    because one provider was busy is worse than one scored by the runner-up.
    """
    user_keys = user_keys or {}
    chain = []
    if requested in VALID_MODELS and requested != scored_model:
        chain.append(requested)
    chain += [k for k in JUDGE_PREFERENCE
              if k != scored_model and k in VALID_MODELS and k not in chain]
    return [k for k in chain if _judge_affordable(k, user_keys)]


# Worth trying the next judge for; a refusal or an unparseable reply is not —
# that is the judge answering, and swapping models to get a different answer
# would be shopping for a score.
_TRANSIENT = {"SERVICE_UNAVAILABLE", "RATE_LIMIT", "TIMEOUT", "USAGE_LIMIT",
              "PROVIDER_ERROR", "EMPTY_RESPONSE", "NO_API_KEY", "QUOTA_EXCEEDED"}


COUNCIL_MAX_TOKENS = 1600       # a verdict and six candidates, nothing more


# A judge that just failed is almost certainly still failing. Without this,
# every scored answer re-tried the same unavailable model first and paid its
# timeout again: scoring two answers took 157 seconds, nearly all of it waiting
# on a Gemini tier that was 503-ing on every call. Short enough that a brief
# outage does not sideline a model for the session.
_JUDGE_COOLDOWN = 120          # seconds
_judge_failed_at: dict = {}


def _on_cooldown(key: str) -> bool:
    ts = _judge_failed_at.get(key)
    return ts is not None and (time.monotonic() - ts) < _JUDGE_COOLDOWN


async def _run_chain(chain, prompt, system, max_tokens, user_keys):
    """First judge in the chain that actually answers."""
    last = {"error": "NO_JUDGE_AVAILABLE", "message": "No judge answered"}

    # Try the ones not in cooldown first, but keep the rest as a fallback so a
    # cooldown can never leave the chain empty.
    ordered = [k for k in chain[:4] if not _on_cooldown(k)] + \
              [k for k in chain[:4] if _on_cooldown(k)]

    for key in ordered:
        try:
            res = await asyncio.wait_for(
                _run_model(key, prompt, system, "", [], [],
                           max_tokens, user_keys=user_keys, web_search=False),
                timeout=JUDGE_TIMEOUT)
        except asyncio.TimeoutError:
            _judge_failed_at[key] = time.monotonic()
            last = {"error": "TIMEOUT", "message": f"{key} did not answer in {JUDGE_TIMEOUT}s"}
            continue
        except Exception as e:
            _judge_failed_at[key] = time.monotonic()
            last = {"error": "JUDGE_FAILED", "message": str(e)[:200]}
            continue
        if res.get("status") == "ok":
            _judge_failed_at.pop(key, None)
            return key, res, None
        last = {"error": res.get("error") or "JUDGE_FAILED",
                "message": res.get("message") or "The judge model did not answer"}
        if res.get("error") in _TRANSIENT:
            _judge_failed_at[key] = time.monotonic()
        else:
            break                      # a refusal is the judge answering
    return None, None, last


@agent_bp.route("/agent/council", methods=["POST", "OPTIONS"])
@require_auth
def council():
    """One structured verdict across every model's answer to the same question.

    Returns a headline, a short summary, and ranked candidates — rather than
    the essay the previous synthesis produced, which left the reader to work
    out the conclusion themselves.
    """
    data = request.get_json(silent=True) or {}
    answers = {k: v for k, v in (data.get("answers") or {}).items()
               if isinstance(v, str) and v.strip()}
    if not answers:
        return jsonify({"error": "MISSING_ANSWERS",
                        "message": "Provide an 'answers' object of model → text"}), 400
    if len(answers) > MAX_ANSWERS:
        return jsonify({"error": "TOO_MANY_ANSWERS",
                        "message": f"At most {MAX_ANSWERS} answers per request"}), 400

    question = (data.get("question") or "").strip()
    focus = (data.get("focus") or "").strip()[:2000]
    verifications = data.get("verifications") or {}
    user_keys = data.get("user_keys") or {}

    chain = _judge_chain("", data.get("judge"), user_keys)
    if not chain:
        return jsonify({"error": "NO_JUDGE_AVAILABLE",
                        "message": "No judge model is configured"}), 503

    # The council gathers its own evidence before judging: every candidate any
    # answer named is profiled against PubMed and ClinicalTrials.gov, so it can
    # rank on the record rather than on which answer argued most fluently.
    try:
        profiles = asyncio.run(lit.profile_candidates(answers, question))
    except Exception:
        profiles = []

    prompt = agent_service.build_council_prompt(question, answers, verifications,
                                                focus, profiles)

    try:
        judge, result, failure = asyncio.run(_run_chain(
            chain, prompt, agent_service.COUNCIL_SYSTEM, COUNCIL_MAX_TOKENS, user_keys))
    except Exception as e:
        return jsonify({"error": "COUNCIL_FAILED", "message": str(e)[:200]}), 502

    if not result:
        return jsonify({**failure, "tried": chain[:4]}), 502

    verdict = agent_service.parse_council_reply(result.get("text") or "")
    if not verdict:
        # Rather than fail outright, hand back the raw text so the box can still
        # show something — a readable paragraph beats an error message.
        return jsonify({"status": "ok", "verdict": None,
                        "raw": (result.get("text") or "")[:4000],
                        "judge": judge, "models_considered": len(answers)}), 200

    return jsonify({"status": "ok", "verdict": verdict, "judge": judge,
                    "judge_served_model": result.get("served_model"),
                    "models_considered": len(answers),
                    "candidates_profiled": profiles,
                    "usage": result.get("usage")}), 200


@agent_bp.route("/agent/score-all", methods=["POST", "OPTIONS"])
@require_auth
def score_all():
    """Score every model's answer to the same question, in one pass.

    One request rather than one per box. Scoring them together is also the only
    way the numbers are comparable: the same judge, the same rubric, the same
    question. Scored one at a time the judge could differ between boxes, and a
    difference between two models would partly measure their judges.
    """
    data = request.get_json(silent=True) or {}
    answers = {k: v for k, v in (data.get("answers") or {}).items()
               if isinstance(v, str) and v.strip()}
    if not answers:
        return jsonify({"error": "MISSING_ANSWERS",
                        "message": "Provide an 'answers' object of model → text"}), 400
    if len(answers) > MAX_ANSWERS:
        return jsonify({"error": "TOO_MANY_ANSWERS",
                        "message": f"At most {MAX_ANSWERS} answers per request"}), 400

    question = (data.get("question") or "").strip()
    user_keys = data.get("user_keys") or {}
    verifications = data.get("verifications") or {}

    # One judge for the whole batch, resolved before any scoring starts.
    #
    # Two reasons. Comparability: scored by different judges, a gap between two
    # models would partly be a gap between their judges, which is exactly the
    # confound this panel exists to avoid. And speed: scoring concurrently, every
    # answer raced to try the same unavailable model before any of them had
    # recorded the failure, so the per-judge cooldown never engaged and two
    # answers took 157 seconds. Resolving once pays that cost at most once.
    #
    # The judge must not be any model being scored — a model marking its own
    # work is self-preference, not a measurement.
    preferred = [k for k in _judge_chain("", data.get("judge"), user_keys) if k not in answers]
    if not preferred:
        return jsonify({"error": "NO_JUDGE_AVAILABLE",
                        "message": "Every available judge is also being scored"}), 503

    async def run_all():
        # Probe with the shortest answer so a failing judge is discovered cheaply.
        probe_key = min(answers, key=lambda k: len(answers[k]))
        probe_prompt = agent_service.build_judge_prompt(
            question, answers[probe_key], verifications.get(probe_key))
        judge, res, fail = await _run_chain(preferred, probe_prompt,
                                            agent_service.JUDGE_SYSTEM,
                                            JUDGE_MAX_TOKENS, user_keys)
        if not res:
            return {k: {"error": (fail or {}).get("error", "JUDGE_FAILED")} for k in answers}

        async def score_one(model_key: str, answer: str):
            verification = verifications.get(model_key)
            if verification is None:
                try:
                    verification = await lit.verify_citations(answer)
                except Exception:
                    verification = None
            if model_key == probe_key:
                out = res
            else:
                prompt = agent_service.build_judge_prompt(question, answer, verification)
                try:
                    out = await asyncio.wait_for(
                        _run_model(judge, prompt, agent_service.JUDGE_SYSTEM, "", [], [],
                                   JUDGE_MAX_TOKENS, user_keys=user_keys, web_search=False),
                        timeout=JUDGE_TIMEOUT)
                except asyncio.TimeoutError:
                    return model_key, {"error": "TIMEOUT", "judge": judge}
                if out.get("status") != "ok":
                    return model_key, {"error": out.get("error", "JUDGE_FAILED"), "judge": judge}
            parsed = agent_service.parse_judge_reply(out.get("text") or "")
            if not parsed:
                return model_key, {"error": "UNPARSEABLE_SCORE", "judge": judge}
            return model_key, {**parsed, "judge": judge,
                               "saw_verification": bool(verification)}

        pairs = await asyncio.gather(
            *[score_one(k, v) for k, v in answers.items()], return_exceptions=True)
        return {k: v for p in pairs if isinstance(p, tuple) for k, v in [p]}

    try:
        scores = asyncio.run(run_all())
    except Exception as e:
        return jsonify({"error": "SCORING_FAILED", "message": str(e)[:200]}), 502

    return jsonify({"status": "ok", "scores": scores,
                    "scored": sum(1 for s in scores.values() if "overall" in s)}), 200


@agent_bp.route("/agent/score", methods=["POST", "OPTIONS"])
@require_auth
def score_answer():
    data = request.get_json(silent=True) or {}
    answer = (data.get("answer") or "").strip()
    question = (data.get("question") or "").strip()
    scored_model = data.get("model") or ""

    if not answer:
        return jsonify({"error": "MISSING_ANSWER",
                        "message": "Provide the 'answer' text to score"}), 400

    # {model: api_key} forwarded from the caller, same shape /ask uses.
    user_keys = data.get("user_keys") or {}

    chain = _judge_chain(scored_model, data.get("judge"), user_keys)
    if not chain:
        return jsonify({"error": "NO_JUDGE_AVAILABLE",
                        "message": "No judge model is configured"}), 503

    # Layer 1 first: its findings are evidence for the judge, so the judge is
    # never guessing at whether a PMID resolves.
    use_verification = data.get("use_verification", True)
    verification = None
    if use_verification:
        try:
            verification = asyncio.run(lit.verify_citations(answer))
        except Exception:
            verification = None

    prompt = agent_service.build_judge_prompt(question, answer, verification)

    async def _judge() -> tuple:
        last = {"error": "NO_JUDGE_AVAILABLE", "message": "No judge answered"}
        for key in chain[:4]:
            try:
                res = await _run_model(key, prompt, agent_service.JUDGE_SYSTEM, "", [], [],
                                       JUDGE_MAX_TOKENS, user_keys=user_keys, web_search=False)
            except Exception as e:
                last = {"error": "JUDGE_FAILED", "message": str(e)[:200]}
                continue
            if res.get("status") == "ok":
                return key, res, None
            last = {"error": res.get("error") or "JUDGE_FAILED",
                    "message": res.get("message") or "The judge model did not answer"}
            if res.get("error") not in _TRANSIENT:
                break
        return None, None, last

    try:
        judge, result, failure = asyncio.run(_judge())
    except Exception as e:
        return jsonify({"error": "JUDGE_FAILED", "message": str(e)[:200]}), 502

    if not result:
        return jsonify({**failure, "tried": chain[:4]}), 502

    score = agent_service.parse_judge_reply(result.get("text") or "")
    if not score:
        # Never invent a score from an unparseable reply — a fabricated number
        # would flow straight into the reliability statistics.
        return jsonify({"error": "UNPARSEABLE_SCORE",
                        "message": "The judge did not return a readable score",
                        "judge": judge}), 502

    return jsonify({
        "status": "ok",
        "ai_score": score,                  # Layer 2 — kept separate…
        "verification": verification,       # …from Layer 1
        "judge": judge,
        "judge_served_model": result.get("served_model"),
        "saw_verification": bool(verification),
        "usage": result.get("usage"),
    }), 200
