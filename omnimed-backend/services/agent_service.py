# Layer 2: automated scoring of a model's answer by another model.
#
# Layer 1 (literature_service) answers "are the citations real and on-topic?"
# — deterministic, from public registries. It cannot judge whether the science
# is any good. That is what this layer attempts, and it is the layer that has
# to be treated with suspicion, for two reasons:
#
#   Circularity — a model scoring model output is not an independent measure.
#   Self-preference — models rate their own writing higher than others'.
#
# Two things are done about that. The judge is never told which model wrote
# the answer, so it cannot prefer itself by name. And the score produced here
# is stored in its own column, never merged with the deterministic result or
# with the researcher's wet-lab verdict. Keeping the three apart is what makes
# the real question answerable: does the automated score predict the wet-lab
# outcome? A blended number could never answer that.
#
# Nothing here makes a network call — prompt building and parsing are pure so
# the rubric can be tested without spending tokens.

import re
import json

# Kept deliberately small. Every extra dimension is extra output tokens on
# every scored answer, and dimensions that correlate with each other add cost
# without adding signal.
RUBRIC = [
    ("evidence",     "Are the claims supported by real, relevant, correctly-attributed sources?"),
    ("mechanism",    "Is the proposed biological mechanism specific and coherent, not hand-waving?"),
    ("actionability", "Could a wet-lab researcher act on this — named target, model system, assay?"),
    ("calibration",  "Does the stated confidence match the strength of the evidence offered?"),
]

DIMENSIONS = [k for k, _ in RUBRIC]

MAX_ANSWER_CHARS = 6_000
MAX_QUESTION_CHARS = 1_000

JUDGE_SYSTEM = (
    "You are a critical peer reviewer for a cardiovascular molecular biology lab. "
    "You are scoring one candidate answer to a research question.\n\n"
    "You are NOT told which system produced the answer, and you must not speculate "
    "about it. Judge only the content in front of you.\n\n"
    "Be harsh. A fluent, confident answer with vague mechanisms and unsupported "
    "claims is a BAD answer and must score low — fluency is not evidence. Reserve "
    "8-10 for answers a researcher could act on this week without further reading.\n\n"
    "Reply with ONLY a JSON object, no prose and no code fence:\n"
    '{"evidence":N,"mechanism":N,"actionability":N,"calibration":N,'
    '"overall":N,"rationale":"one or two sentences","red_flags":["..."]}\n'
    "Every N is an integer from 1 to 10. `overall` is your holistic judgement, "
    "not an average. `red_flags` lists specific problems and is [] if there are none."
)


# ── Council: one verdict across every model's answer ───────────────────────
#
# The old synthesis returned prose, which put the reader back where they
# started — several screens of text to read before knowing what the models
# actually agreed on. This asks for structure instead: the answer first, then
# the candidates ranked by how many models backed them and whether their
# citations survived verification, then the points of disagreement.
#
# Agreement between models is weak evidence on its own — models share training
# data, so they can be wrong together — which is why the verified-citation
# count travels beside the agreement count rather than being folded into it.

COUNCIL_SYSTEM = (
    "You are chairing a review of several independent answers to the same "
    "biomedical research question. Each answer came from a different AI model. "
    "You are given the answers and, where available, the result of checking "
    "their citations against PubMed and Crossref.\n\n"
    "Your job is to tell a researcher what to conclude, briefly.\n\n"
    "Rules:\n"
    "- Lead with the single most important finding, in one sentence.\n"
    "- Rank candidates by the strength of the evidence behind them, NOT by how "
    "many models mentioned them. Models share training data and are wrong "
    "together; agreement alone is weak evidence.\n"
    "- A candidate supported by verified citations outranks one backed only by "
    "model consensus. Say so when that happens.\n"
    "- Name real disagreements between the models. Do not smooth them over.\n"
    "- Be brief. Every field is read at a glance, not studied.\n\n"
    "Reply with ONLY this JSON object, no prose and no code fence:\n"
    '{"headline":"one sentence, the answer itself",'
    '"summary":"2-3 sentences of context",'
    '"candidates":[{"name":"e.g. miR-132","models":N,"confidence":"high|moderate|low",'
    '"why":"one short sentence","caveat":"one short sentence or empty"}],'
    '"disagreements":["short statement of a real conflict"],'
    '"caveats":["short limitation a researcher should know"]}\n'
    "`models` is how many of the answers proposed that candidate.\n"
    "- Include EVERY candidate listed in the independent evidence section, up "
    "to 8. Do not silently drop one because only a single answer named it — a "
    "candidate one model found and the others missed is worth reporting, "
    "marked low confidence if the evidence is thin.\n"
    "At most 8 candidates, 3 disagreements and 3 caveats."
)

MAX_COUNCIL_ANSWER_CHARS = 3_500


def _candidate_table(profiles: list | None) -> str:
    """Independently gathered evidence for every candidate any model named.

    This is the council's own evidence rather than the models' — it is what
    lets it rank a candidate that only one model proposed above one that four
    of them agreed on, which plain synthesis of their prose cannot do.
    """
    if not profiles:
        return ""
    rows = []
    for p in profiles:
        papers = p.get("papers")
        recent = p.get("papers_recent")
        trials = p.get("trials")
        bits = [f"named by {p.get('n_models', 0)} of the answers"]
        bits.append("literature: unknown (lookup failed)" if papers is None
                    else f"{papers} PubMed papers"
                         + (f", {recent} since 2021" if recent is not None else ""))
        bits.append("trials: unknown" if trials is None
                    else f"{trials} registered trial(s) using it as an intervention")
        if p.get("trial_example"):
            bits.append(f"e.g. {p['trial_example']}")
        rows.append(f"- {p.get('name')}: " + "; ".join(bits))

    return ("\n\nINDEPENDENT EVIDENCE (gathered from PubMed and ClinicalTrials.gov, "
            "not from the answers — treat as fact):\n" + "\n".join(rows) +
            "\n\nEvery candidate any answer named is listed above, including ones "
            "only a single answer proposed. Judge each on this evidence. A "
            "candidate with trials or a substantial recent literature may deserve "
            "to outrank one that more answers happened to mention.")


def build_council_prompt(question: str, answers: dict,
                         verifications: dict | None = None,
                         focus: str | None = None,
                         profiles: list | None = None) -> str:
    """Assemble every model's answer, each tagged with its own citation record.

    Answers are labelled by index rather than by model name. The council should
    weigh what was said and whether it checks out, and naming the models invites
    it to defer to whichever brand it rates highest.
    """
    verifications = verifications or {}
    blocks = []
    for i, (key, answer) in enumerate(answers.items(), start=1):
        v = verifications.get(key) or {}
        if v.get("checked"):
            cite = (f"[citations: {v.get('verified', 0)} verified, "
                    f"{v.get('fabricated', 0)} fabricated, "
                    f"{v.get('mismatched', 0)} real but unrelated to the claim]")
        else:
            cite = "[citations: none given or not checked]"
        blocks.append(f"<answer id=\"{i}\">\n{cite}\n"
                      f"{_shorten(answer, MAX_COUNCIL_ANSWER_CHARS)}\n</answer>")

    out = (f"QUESTION:\n{_shorten(question, MAX_QUESTION_CHARS)}\n\n"
           + "\n\n".join(blocks) + _candidate_table(profiles))
    if focus:
        out += ("\n\nThe reader also asked: " + _shorten(focus, 400)
                + "\nAnswer that in `summary`, using only what the answers above contain.")
    return out + "\n\nReturn the JSON object now."


def parse_council_reply(text: str) -> dict | None:
    """Pull the council verdict out of a reply, tolerating fences and prose."""
    obj = _locate_json(text)
    if not obj or not isinstance(obj, dict):
        return None
    if not (obj.get("headline") or obj.get("candidates")):
        return None

    def _str(v, limit):
        return str(v or "").strip()[:limit]

    cands = []
    for c in (obj.get("candidates") or [])[:6]:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        conf = str(c.get("confidence") or "").lower()
        try:
            n = int(c.get("models") or 0)
        except (TypeError, ValueError):
            n = 0
        cands.append({
            "name": _str(c.get("name"), 60),
            "models": max(0, n),
            "confidence": conf if conf in ("high", "moderate", "low") else "moderate",
            "why": _str(c.get("why"), 240),
            "caveat": _str(c.get("caveat"), 240),
        })

    def _list(key, limit, n):
        raw = obj.get(key)
        if isinstance(raw, str):
            raw = [raw]
        return [_str(x, limit) for x in (raw or [])[:n] if str(x).strip()]

    return {
        "headline": _str(obj.get("headline"), 400),
        "summary": _str(obj.get("summary"), 900),
        "candidates": cands,
        "disagreements": _list("disagreements", 300, 3),
        "caveats": _list("caveats", 300, 3),
    }


def _shorten(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    return f"{text[:head]}\n\n[…trimmed…]\n\n{text[-(limit - head):]}"


def _locate_json(text: str):
    """Find the JSON object in a model reply.

    Models wrap JSON in prose or code fences however often they are asked not
    to, so the object is located rather than assumed to be the whole reply.
    The outermost braces are tried first: a non-greedy scan would stop at the
    first nested object and lose everything after it.
    """
    if not text:
        return None
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()

    candidates = [raw]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    candidates += re.findall(r"\{.*?\}", raw, re.S)

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _verification_digest(verification: dict | None) -> str:
    """Layer 1's findings, compressed to the few lines the judge needs.

    The judge is given facts rather than asked to guess at them: whether a PMID
    resolves is already known deterministically, and a model asked to guess
    would invent an answer. Passing the digest is recorded on the result, so an
    analysis can account for the fact that this score saw the Layer 1 evidence.
    """
    if not verification or not verification.get("checked"):
        return "Citation check: the answer cited no verifiable sources."

    lines = [
        f"Citation check (deterministic, from PubMed/Crossref — treat as fact): "
        f"{verification['checked']} cited; {verification.get('verified', 0)} real; "
        f"{verification.get('fabricated', 0)} do not exist; "
        f"{verification.get('unresolved', 0)} unconfirmed; "
        f"{verification.get('mismatched', 0)} real but unrelated to the claim."
    ]
    for c in (verification.get("citations") or []):
        support = (c.get("support") or {}).get("support")
        if c.get("exists") is False:
            lines.append(f"  - {c.get('id')}: DOES NOT EXIST")
        elif support in ("mismatched", "unrelated"):
            lines.append(f"  - {c.get('id')}: real, but does not support the claim "
                         f"({(c.get('support') or {}).get('reason', '')[:110]})")
    return "\n".join(lines[:14])


def build_judge_prompt(question: str, answer: str,
                       verification: dict | None = None) -> str:
    """The user-turn content for the judge. No model identity appears anywhere."""
    criteria = "\n".join(f"- {k}: {desc}" for k, desc in RUBRIC)
    return (
        f"RESEARCH QUESTION:\n{_shorten(question, MAX_QUESTION_CHARS)}\n\n"
        f"CANDIDATE ANSWER:\n{_shorten(answer, MAX_ANSWER_CHARS)}\n\n"
        f"{_verification_digest(verification)}\n\n"
        f"Score on each criterion:\n{criteria}\n\n"
        "Return the JSON object now."
    )


def _clamp(v) -> int | None:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(1, min(10, n))


def parse_judge_reply(text: str) -> dict | None:
    """Pull the score object out of a judge reply.

    Models wrap JSON in prose or code fences however often they are asked not
    to, so the object is located rather than assumed to be the whole reply.
    """
    obj = _locate_json(text)
    if not isinstance(obj, dict) or not ("overall" in obj or
                                         any(d in obj for d in DIMENSIONS)):
        return None

    scores = {d: _clamp(obj.get(d)) for d in DIMENSIONS}
    judge_overall = _clamp(obj.get("overall"))
    present = [v for v in scores.values() if v is not None]

    # The overall is the average of the four dimensions, not the judge's own
    # holistic number. The holistic number drifted from the dimensions it
    # summarised: an answer that declined to answer scored mechanism 1 and
    # actionability 3, yet 9/10 overall for its honesty. An average is
    # reproducible and explainable ("the mean of the four"). The judge's own
    # figure is kept as judge_overall for the record.
    if present:
        overall = _clamp(sum(present) / len(present))
    elif judge_overall is not None:
        overall = judge_overall
    else:
        return None

    flags = obj.get("red_flags")
    if isinstance(flags, str):
        flags = [flags]
    elif not isinstance(flags, list):
        flags = []

    return {
        "overall": overall,
        "judge_overall": judge_overall,
        "scores": scores,
        "rationale": str(obj.get("rationale") or "").strip()[:600],
        "red_flags": [str(f)[:200] for f in flags[:6]],
    }
