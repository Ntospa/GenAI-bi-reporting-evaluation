"""Evaluation layer: aggregate quantitative metrics (RQ1/RQ2) and human rubric scores (RQ3).

Design contract (locked - see config.yaml `rq3` and the methodology chapter of the dissertation):
  * RQ1 primary condition   = rag_on, scenarios S1-S3 (reported separately).
  * RAG-context ablation     = rag_off vs rag_on (S1-S3), reported separately.
  * Robustness (AI-only)     = S4 (injected ground-truth), reported separately, not in RQ1 headline.
  * RQ2 consistency          = 5 runs/scenario; also positions the pre-specified eval_primary
                               run (rag_on, run 1) within the 5-run distribution (safeguard).
  * RQ3 qualitative          = descriptive/exploratory only (n=3 scenarios): per-source
                               descriptives + Cliff's delta effect sizes (bootstrap CI) +
                               Krippendorff's alpha as a reliability check (bootstrap CI).
                               no null-hypothesis significance tests, no multilevel models -
                               the number of scenarios does not support them.
All quantitative metrics cover numeric claims only (see validate.py).
"""
from __future__ import annotations
import csv
import json
import os
import random
from itertools import combinations

# Fallback rubric dimensions if config is unavailable (kept in sync with config.yaml `rq3`).
RUBRIC_COLS = ["factual_accuracy", "completeness", "analytical_reasoning",
               "clarity_coherence", "decision_usefulness", "trustworthiness"]

_ROBUSTNESS_SID = "S4_synthetic_groundtruth"


def select_eval_run(faiths: list[float], eval_primary: dict | None = None) -> int:
    """Deterministically pick the 0-based index of the AI run used for the RQ3 blind set.

    select == 'median' (default): the median-faithfulness run (representative, central - not max),
        ties resolved to the lowest seed/index. For odd N this is a real run.
    else if `run: <n>` is given: the fixed 1-based index n-1 (legacy fixed-instance rule).
    """
    eval_primary = eval_primary or {"select": "median"}
    if not faiths:
        return 0
    if eval_primary.get("select") == "median" or "run" not in eval_primary:
        sv = sorted(faiths)
        med = sv[len(sv) // 2]
        return min(i for i, f in enumerate(faiths) if f == med)  # tie -> lowest index (seed)
    return max(0, min(len(faiths) - 1, eval_primary.get("run", 1) - 1))


# --------------------------------------------------------------------------------------
# small pure-python stats helpers (no scipy dependency)
# --------------------------------------------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    if not xs:
        return None
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _median(xs):
    if not xs:
        return None
    s = sorted(xs); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def cliffs_delta(a: list[float], b: list[float]):
    """Cliff's delta effect size for a vs b (non-parametric, ordinal-safe).
    delta in [-1, 1]: >0 means group `a` tends to score higher. Returns None if either empty."""
    if not a or not b:
        return None
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    return (gt - lt) / (len(a) * len(b))


def _bootstrap_ci(fn, groups, B=2000, seed=0, alpha=0.05):
    """Percentile bootstrap CI for a statistic `fn(*resampled_groups)`.
    `groups` is a tuple of lists; each is resampled with replacement independently."""
    rng = random.Random(seed)
    stats = []
    for _ in range(B):
        res = tuple([g[rng.randrange(len(g))] for _ in range(len(g))] for g in groups)
        try:
            v = fn(*res)
        except Exception:
            v = None
        if v is not None:
            stats.append(v)
    if not stats:
        return None
    stats.sort()
    lo = stats[int((alpha / 2) * len(stats))]
    hi = stats[min(len(stats) - 1, int((1 - alpha / 2) * len(stats)))]
    return [round(lo, 4), round(hi, 4)]


def krippendorff_alpha(items: list[list[float]], level: str = "ordinal"):
    """Krippendorff's alpha over reliability data.
    `items`: one list of ratings per unit (missing values already dropped). Units with <2
    ratings are ignored. Returns None if not estimable.

    Implements the coincidence-matrix formulation with an ordinal (default) or interval metric.
    Reported as a reliability check only, with a bootstrap CI, given the small item count.
    """
    units = [u for u in items if len([x for x in u if x is not None]) >= 2]
    if not units:
        return None
    # observed values domain
    vals = sorted({x for u in units for x in u if x is not None})
    if len(vals) < 2:
        return 1.0  # all identical -> perfect agreement by convention
    idx = {v: i for i, v in enumerate(vals)}

    # coincidence matrix o[c][k]
    K = len(vals)
    o = [[0.0] * K for _ in range(K)]
    for u in units:
        obs = [x for x in u if x is not None]
        m = len(obs)
        for i in range(m):
            for j in range(m):
                if i != j:
                    o[idx[obs[i]]][idx[obs[j]]] += 1.0 / (m - 1)
    n_c = [sum(o[c]) for c in range(K)]
    n = sum(n_c)
    if n <= 1:
        return None

    def delta2(c, k):
        if level == "interval":
            return (vals[c] - vals[k]) ** 2
        # ordinal metric
        lo, hi = (c, k) if c <= k else (k, c)
        s = sum(n_c[g] for g in range(lo, hi + 1)) - (n_c[lo] + n_c[hi]) / 2.0
        return s ** 2

    Do = sum(o[c][k] * delta2(c, k) for c in range(K) for k in range(K)) / n
    De = sum(n_c[c] * n_c[k] * delta2(c, k) for c in range(K) for k in range(K)) / (n * (n - 1))
    if De == 0:
        return 1.0
    return 1.0 - Do / De


# --------------------------------------------------------------------------------------
# RQ1 / RQ2 quantitative aggregation
# --------------------------------------------------------------------------------------
def _parse_key(key):
    sid, _, tag = key.partition("::")
    return sid, tag


def aggregate_quantitative(per_scenario: dict, eval_primary: dict | None = None) -> dict:
    """per_scenario: {"<sid>::<tag>": {"runs": [validation dicts], "consistency": {...}}}
    Separates RQ1 primary (rag_on, S1-S3) from the rag_off ablation and the AI-only S4."""
    eval_primary = eval_primary or {"tag": "rag_on", "run": 1}
    out = {"scenarios": {}, "rq1_primary": {}, "ablation": {}, "robustness": {},
           "rq2_eval_instance_positioning": {}}
    prim_f, prim_h, prim_c = [], [], []

    for key, data in per_scenario.items():
        sid, tag = _parse_key(key)
        runs = data["runs"]
        f = [r["numeric_claim_faithfulness"] for r in runs]
        h = [r["numeric_hallucination_rate"] for r in runs]
        c = [r["kpi_coverage"] for r in runs]
        block = {
            "numeric_claim_faithfulness_mean": round(_mean(f), 4),
            "numeric_hallucination_rate_mean": round(_mean(h), 4),
            "kpi_coverage_mean": round(_mean(c), 4),
            "consistency": data.get("consistency", {}),
        }
        out["scenarios"][key] = block

        is_robustness = sid == _ROBUSTNESS_SID
        if is_robustness:
            out["robustness"][key] = block
        elif tag == "rag_off":
            out["ablation"][key] = block
        elif tag == eval_primary.get("tag"):          # rag_on, S1-S3 = RQ1 primary
            prim_f += f; prim_h += h; prim_c += c
            # RQ2 safeguard: position of the selected blind-eval run within the 5-run distribution.
            ridx = select_eval_run(f, eval_primary)
            if 0 <= ridx < len(f):
                mu, sd = _mean(f), _std(f)
                pctl = sum(1 for x in f if x <= f[ridx]) / len(f)
                out["rq2_eval_instance_positioning"][sid] = {
                    "selection_rule": eval_primary.get("select", "run:%s" % eval_primary.get("run")),
                    "selected_run": ridx + 1,
                    "run_faithfulness": round(f[ridx], 4),
                    "runs_mean": round(mu, 4), "runs_std": round(sd, 4),
                    "z_vs_runs": round((f[ridx] - mu) / sd, 3) if sd else 0.0,
                    "percentile_within_runs": round(pctl, 3),
                    "note": "position of the blind-eval AI instance among its 5 runs (central if |z|<~1)",
                }

    out["rq1_primary"]["overall"] = {
        "condition": "rag_on, scenarios S1-S3 (AI-only S4 and rag_off ablation excluded)",
        "numeric_claim_faithfulness_mean": round(_mean(prim_f), 4) if prim_f else None,
        "numeric_hallucination_rate_mean": round(_mean(prim_h), 4) if prim_h else None,
        "kpi_coverage_mean": round(_mean(prim_c), 4) if prim_c else None,
        "n_runs": len(prim_f),
    }
    return out


# --------------------------------------------------------------------------------------
# RQ3 rubric aggregation
# --------------------------------------------------------------------------------------
def _load_dims(cfg=None):
    try:
        return list((cfg or {}).get("rq3", {}).get("dimensions") or RUBRIC_COLS)
    except Exception:
        return RUBRIC_COLS


def _load_key(key_path):
    """report_id -> source ('human'/'ai') from the private blind key."""
    if not key_path or not os.path.exists(key_path):
        return {}
    return {r["neutral_id"]: r["true_source"].lower()
            for r in csv.DictReader(open(key_path, encoding="utf-8"))}


def aggregate_rubric(csv_path: str, key_path: str | None = None, cfg=None) -> dict:
    """Aggregate blind rubric scores. Raters fill a source-free CSV keyed by neutral report_id;
    the private blind key maps report_id -> true source for analysis.

    rubric CSV columns: rater_id, report_id, <dimensions...>, source_guess, free-text...
    Returns descriptives by source, Cliff's delta (AI vs human) with bootstrap CI per dimension,
    and Krippendorff's alpha (reliability check) with bootstrap CI per dimension.
    """
    dims = _load_dims(cfg)
    if not os.path.exists(csv_path):
        return {"note": f"no rubric file at {csv_path} (collect blind ratings after ethics approval)",
                "dimensions": dims}
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    if not rows:
        return {"note": f"rubric file {csv_path} is empty", "dimensions": dims}

    src_map = _load_key(key_path)

    def source_of(row):
        return src_map.get(row.get("report_id"), (row.get("source") or "").lower())

    # Observed (not planned) participant counts: the analysis is driven entirely by what was
    # actually collected, so a realised rater n below the recruitment target changes nothing here.
    result = {"dimensions": dims, "n_ratings": len(rows),
              "n_raters_observed": len({r.get("rater_id") for r in rows if r.get("rater_id")}),
              "n_reports_observed": len({r.get("report_id") for r in rows if r.get("report_id")}),
              "by_source": {},
              "effect_size_cliffs_delta_ai_vs_human": {}, "inter_rater_krippendorff_alpha": {},
              "method_note": ("DESCRIPTIVE/EXPLORATORY: descriptives + Cliff's delta (bootstrap CI) "
                              "+ Krippendorff's alpha (reliability check, bootstrap CI). "
                              "No NHST, no multilevel models (n=3 scenarios).")}

    # descriptives by source × dimension
    by_source = {}
    for r in rows:
        by_source.setdefault(source_of(r), []).append(r)
    for src, rs in by_source.items():
        d = {}
        for col in dims:
            vals = [float(x[col]) for x in rs if x.get(col) not in (None, "")]
            d[col] = {"mean": round(_mean(vals), 3) if vals else None,
                      "std": round(_std(vals), 3) if vals else None,
                      "median": _median(vals), "n": len(vals)}
        result["by_source"][src or "unknown"] = d

    # Cliff's delta (AI vs human) per dimension, with bootstrap CI
    for col in dims:
        ai = [float(x[col]) for x in rows if source_of(x) == "ai" and x.get(col) not in (None, "")]
        hu = [float(x[col]) for x in rows if source_of(x) == "human" and x.get(col) not in (None, "")]
        if ai and hu:
            d = cliffs_delta(ai, hu)
            result["effect_size_cliffs_delta_ai_vs_human"][col] = {
                "delta": round(d, 4) if d is not None else None,
                "ci95": _bootstrap_ci(cliffs_delta, (ai, hu)),
                "n_ai": len(ai), "n_human": len(hu),
                "note": "delta>0: AI rated higher. Exploratory only (small n).",
            }

    # Krippendorff's alpha (ordinal) per dimension over rater x report grid, with bootstrap CI
    for col in dims:
        grid = {}
        raters = sorted({r["rater_id"] for r in rows})
        for r in rows:
            if r.get(col) not in (None, ""):
                grid.setdefault(r["report_id"], {})[r["rater_id"]] = float(r[col])
        items = [[g.get(rt) for rt in raters] for g in grid.values()]
        alpha = krippendorff_alpha(items, level="ordinal")
        ci = _bootstrap_ci(lambda it: krippendorff_alpha(it, "ordinal"), (items,)) if items else None
        result["inter_rater_krippendorff_alpha"][col] = {
            "alpha": round(alpha, 4) if alpha is not None else None,
            "ci95": ci, "n_items": len(items),
            "note": "reliability check; unstable with few items - interpret with caution.",
        }
    return result


def write_summary(quant: dict, rubric: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"quantitative": quant, "rubric": rubric}, f, ensure_ascii=False, indent=2)
