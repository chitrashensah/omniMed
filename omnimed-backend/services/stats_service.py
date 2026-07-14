# Statistics for the wet-lab validation research report.
# Descriptive (N, mean, SD, SEM, median, 95% CI) per model, plus inferential
# tests (one-way ANOVA + pairwise Welch t-tests) when there's enough data.
# All results are computed to be publication-appropriate and honest about power.

import math
import warnings
from statistics import mean, median, stdev

# scipy warns on t-tests over near-identical data (a model scored e.g. 8,8,8 →
# zero variance). That's a legitimate real-world case; the result is still valid.
warnings.filterwarnings("ignore", message="Precision loss occurred", category=RuntimeWarning)

try:
    from scipy import stats as _scipy
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False

MODEL_LABELS = {
    "claude": "Claude Sonnet 4.6", "gpt4": "GPT-4o", "gemini": "Gemini 2.5 Flash",
    "deepseek": "DeepSeek V3", "groq": "Llama 3.3 70B", "qwen": "Qwen 2.5 72B",
    "cohere": "Command R+",
}

MIN_N_FOR_POWER = 5   # below this per group, flag as underpowered


def _parse_scores(rows: list) -> dict:
    """Group numeric 1-10 verdicts by model. Returns {model: [scores]}."""
    groups: dict[str, list[float]] = {}
    for r in rows:
        m = r.get("model")
        v = r.get("verdict")
        if not m or v is None:
            continue
        try:
            score = float(v)
        except (TypeError, ValueError):
            continue
        if 1 <= score <= 10:
            groups.setdefault(m, []).append(score)
    return groups


def _ci95(vals: list[float]) -> tuple[float, float] | None:
    """95% confidence interval for the mean (t-based; None if n<2)."""
    n = len(vals)
    if n < 2:
        return None
    m = mean(vals)
    se = stdev(vals) / math.sqrt(n)
    if _HAS_SCIPY:
        tcrit = _scipy.t.ppf(0.975, df=n - 1)
    else:
        tcrit = 1.96  # normal approximation fallback
    return (round(m - tcrit * se, 2), round(m + tcrit * se, 2))


def compute_model_stats(rows: list) -> dict:
    """
    Full statistical summary for the report.
    Returns descriptive per-model stats, an overall ANOVA, pairwise comparisons,
    and power/validity flags.
    """
    groups = _parse_scores(rows)

    descriptive = []
    for model, vals in groups.items():
        n = len(vals)
        sd = round(stdev(vals), 2) if n >= 2 else 0.0
        sem = round(sd / math.sqrt(n), 2) if n >= 2 else 0.0
        ci = _ci95(vals)
        descriptive.append({
            "model": model,
            "label": MODEL_LABELS.get(model, model),
            "n": n,
            "mean": round(mean(vals), 2),
            "sd": sd,
            "sem": sem,
            "median": round(median(vals), 2),
            "min": round(min(vals), 1),
            "max": round(max(vals), 1),
            "ci95_low": ci[0] if ci else None,
            "ci95_high": ci[1] if ci else None,
            "scores": vals,   # raw scores for distribution plots
        })
    # Rank by mean score, highest first
    descriptive.sort(key=lambda d: d["mean"], reverse=True)

    total_n = sum(d["n"] for d in descriptive)
    testable = {m: v for m, v in groups.items() if len(v) >= 2}
    underpowered = any(d["n"] < MIN_N_FOR_POWER for d in descriptive) or len(testable) < 2

    anova = _run_anova(testable)
    pairwise = _run_pairwise(testable)

    return {
        "descriptive": descriptive,
        "total_validations": total_n,
        "n_models": len(descriptive),
        "anova": anova,
        "pairwise": pairwise,
        "underpowered": underpowered,
        "min_n_for_power": MIN_N_FOR_POWER,
        "has_scipy": _HAS_SCIPY,
    }


def _run_anova(testable: dict) -> dict | None:
    """One-way ANOVA across models with n>=2. None if not computable."""
    if not _HAS_SCIPY or len(testable) < 2:
        return None
    try:
        f, p = _scipy.f_oneway(*testable.values())
        if math.isnan(f) or math.isnan(p):
            return None
        return {"f": round(float(f), 3), "p": round(float(p), 4),
                "significant": bool(p < 0.05)}
    except Exception:
        return None


def _run_pairwise(testable: dict) -> list:
    """Pairwise Welch t-tests with Bonferroni-adjusted significance threshold."""
    if not _HAS_SCIPY or len(testable) < 2:
        return []
    models = list(testable.keys())
    pairs = [(a, b) for i, a in enumerate(models) for b in models[i + 1:]]
    if not pairs:
        return []
    alpha_adj = 0.05 / len(pairs)
    out = []
    for a, b in pairs:
        try:
            t, p = _scipy.ttest_ind(testable[a], testable[b], equal_var=False)
            if math.isnan(p):
                continue
            out.append({
                "model_a": MODEL_LABELS.get(a, a),
                "model_b": MODEL_LABELS.get(b, b),
                "mean_diff": round(mean(testable[a]) - mean(testable[b]), 2),
                "t": round(float(t), 3),
                "p": round(float(p), 4),
                "significant": bool(p < alpha_adj),
            })
        except Exception:
            continue
    return out
