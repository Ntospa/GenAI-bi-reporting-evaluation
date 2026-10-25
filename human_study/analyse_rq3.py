"""RQ3 analysis: descriptives by source, Cliff's delta, Krippendorff's alpha, report-level ranking
per scenario and source-guess counts, over all raters in results/rubric_scores.csv.

Inputs (private, produced by the human-study scripts): results/rubric_scores.csv and
reports/blind/blind_key.csv. Output: results/rq3_results.json (aggregated only).

Usage:  python -m human_study.analyse_rq3
"""
from __future__ import annotations

import csv
import json
import os

from src.evaluate import aggregate_rubric, _mean
from src.pipeline import load_cfg, _p


def _report_level(rows, key, dims):
    per = {}
    for r in rows:
        per.setdefault(r["report_id"], []).append(r)
    scen = {}
    for rid, v in per.items():
        m = {d: _mean([float(x[d]) for x in v]) for d in dims}
        m["overall"] = _mean(list(m.values()))
        k = key[rid]
        s = scen.setdefault(k["scenario"], {"ai": None, "human": []})
        if k["true_source"] == "ai":
            s["ai"] = m
        else:
            s["human"].append(m)
    res = {}
    for sid, s in scen.items():
        res[sid] = {}
        for d in dims + ["overall"]:
            hv = sorted(m[d] for m in s["human"])
            ai = s["ai"][d]
            res[sid][d] = {"ai_report": round(ai, 3), "human_mean": round(_mean(hv), 3),
                           "human_min": round(hv[0], 3), "human_max": round(hv[-1], 3),
                           "ai_rank_among_6": 1 + sum(1 for x in hv if x > ai)}
    return res


def run():
    cfg = load_cfg()
    dims = cfg["rq3"]["dimensions"]
    results_dir = _p(cfg, "results_dir")
    key_path = os.path.join(_p(cfg, "reports_dir"), "blind", "blind_key.csv")
    scores_path = os.path.join(results_dir, "rubric_scores.csv")
    key = {r["neutral_id"]: r for r in csv.DictReader(open(key_path, encoding="utf-8"))}
    rows = list(csv.DictReader(open(scores_path, encoding="utf-8")))
    out = aggregate_rubric(scores_path, key_path, cfg)
    out["report_level_by_scenario"] = _report_level(rows, key, dims)
    guess = {"ai": {}, "human": {}}
    for r in rows:
        g = guess[key[r["report_id"]]["true_source"]]
        lab = r.get("source_guess") or "blank"
        g[lab] = g.get(lab, 0) + 1
    out["source_guess_by_true_source"] = guess
    path = os.path.join(results_dir, "rq3_results.json")
    json.dump({"primary_all_raters": out}, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[rq3] {out['n_raters_observed']} raters, {out['n_ratings']} ratings -> {path}")
    return out


if __name__ == "__main__":
    run()
