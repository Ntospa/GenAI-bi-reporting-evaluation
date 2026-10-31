"""Sensitivity analysis of numeric_claim_faithfulness to the matching tolerance (RQ1).

The primary validator (validate.py) credits a numeric claim when it lies within 1% of a source
value, or within 0.5 percentage points for percentages. This script re-scores the same reports
with a strict tolerance: a claim is credited only if it equals a source value to the precision in
which it is written, i.e. within half a unit of its last reported digit (e.g. "94,6%" must lie
within 0.05 of the source value, "1.874.360,29" within 0.005).

Extraction, accepted values (raw KPIs and allowed derivations) and unit checks are identical to
validate.py. Only the tolerance changes. The script reports:
  * mean faithfulness per scenario and overall (rag_on, S1-S3) under both tolerances,
  * every claim credited by the primary tolerance but not by the strict one,
  * the blind-evaluation run that the median rule would select under each tolerance.

It does not change any report, KPI file or primary result. Output is printed and written to
results/sensitivity_strict.json.

Usage:  python -m src.sensitivity_strict
"""
from __future__ import annotations
import glob
import json
import os

from . import validate as val, evaluate as ev
from .pipeline import load_cfg, _p

_EPS = 1e-9


def _candidates_with_decimals(raw: str):
    """(value, decimals) for each plausible reading of a numeric token, mirroring
    validate._candidate_values: en-US, European, and all separators removed."""
    a = raw.replace(" ", "").replace(" ", "")
    out = set()
    readings = (
        (a.replace(",", ""), "."),                       # en-US: '.' is the decimal mark
        (a.replace(".", "").replace(",", "."), ","),     # European: ',' is the decimal mark
    )
    for norm, mark in readings:
        try:
            v = float(norm)
        except ValueError:
            continue
        dec = len(a.rsplit(mark, 1)[1]) if mark in a else 0
        out.add((v, dec))
    stripped = "".join(ch for ch in a if ch not in ".,")
    try:
        out.add((float(stripped), 0))
    except ValueError:
        pass
    return out


def _strict_match(raw, unit, acc_vals):
    for kval, kunit, _kind in acc_vals:
        if unit and kunit and unit != kunit:
            continue
        for v, dec in _candidates_with_decimals(raw):
            if abs(v - kval) <= 0.5 * 10 ** (-dec) + _EPS:
                return True
    return False


def run():
    cfg = load_cfg()
    tol = cfg["validation"]["numeric_tolerance_rel"]
    eval_primary = cfg["experiment"].get("eval_primary", {"tag": "rag_on", "select": "median"})
    runs_root = _p(cfg, "runs_dir")
    reports_ai = os.path.join(_p(cfg, "reports_dir"), "ai")
    tag = eval_primary.get("tag", "rag_on")

    def newest_kpis(sid):
        matches = sorted(glob.glob(os.path.join(runs_root, "*", f"kpis_{sid}.json")), key=os.path.getmtime)
        return matches[-1] if matches else None

    result = {"tolerance_primary": f"{tol:.0%} relative, 0.5 pp for percentages",
              "tolerance_strict": "half a unit of the last reported digit",
              "condition": f"{tag}, scenarios without injected change (S1-S3)",
              "scenarios": {}, "flipped_claims": []}
    loose_all, strict_all = [], []
    for sc in cfg["experiment"]["scenarios"]:
        if sc.get("inject_drop"):
            continue                                    # S4 is outside the RQ1 headline
        sid = sc["id"]
        kpi_path = newest_kpis(sid)
        files = sorted(glob.glob(os.path.join(reports_ai, f"{sid}__{tag}__run*.txt")))
        if not kpi_path or not files:
            continue
        kpis = json.load(open(kpi_path, encoding="utf-8"))
        acc = val._acceptable_values(kpis)
        loose_runs, strict_runs = [], []
        for f in files:
            text = open(f, encoding="utf-8").read()
            loose_runs.append(val.validate_report(text, kpis, rel_tol=tol)["numeric_claim_faithfulness"])
            n = s = 0
            for cands, unit, raw, ctx in val._iter_claims(val._strip_product_names(text, kpis)):
                n += 1
                strict_ok = _strict_match(raw, unit, acc)
                s += strict_ok
                if not strict_ok and val._matches((cands, unit), acc, tol) is not None:
                    result["flipped_claims"].append(
                        {"report": os.path.basename(f), "claim": raw, "unit": unit, "context": ctx})
            strict_runs.append(round(s / n, 4) if n else 1.0)
        sel_l = ev.select_eval_run(loose_runs, eval_primary) + 1
        sel_s = ev.select_eval_run(strict_runs, eval_primary) + 1
        result["scenarios"][sid] = {
            "faithfulness_primary_runs": loose_runs, "faithfulness_strict_runs": strict_runs,
            "faithfulness_primary_mean": round(sum(loose_runs) / len(loose_runs), 4),
            "faithfulness_strict_mean": round(sum(strict_runs) / len(strict_runs), 4),
            "eval_run_primary": sel_l, "eval_run_strict": sel_s}
        loose_all += loose_runs
        strict_all += strict_runs
    result["overall"] = {"faithfulness_primary_mean": round(sum(loose_all) / len(loose_all), 4),
                         "faithfulness_strict_mean": round(sum(strict_all) / len(strict_all), 4),
                         "n_runs": len(loose_all), "n_flipped_claims": len(result["flipped_claims"])}

    for sid, r in result["scenarios"].items():
        print(f"[strict] {sid}: primary {r['faithfulness_primary_mean']:.3f}  strict "
              f"{r['faithfulness_strict_mean']:.3f}  eval run {r['eval_run_primary']} -> {r['eval_run_strict']}")
    o = result["overall"]
    print(f"[strict] overall: primary {o['faithfulness_primary_mean']:.3f}  strict "
          f"{o['faithfulness_strict_mean']:.3f}  ({o['n_flipped_claims']} claims credited only by the primary tolerance)")
    for c in result["flipped_claims"]:
        print(f"  {c['report']}: {c['claim']} {c['unit'] or ''} | {c['context']}")
    out = os.path.join(_p(cfg, "results_dir"), "sensitivity_strict.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[strict] written {os.path.relpath(out)}")
    return result


if __name__ == "__main__":
    run()
