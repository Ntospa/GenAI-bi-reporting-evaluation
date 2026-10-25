"""Re-score existing AI reports with the current validator, without re-calling Ollama.

Use after a validator fix (e.g. locale parsing, Option A derived-value crediting) to refresh
results/summary.json from the reports already in reports/ai/, using the KPIs saved in the run dir.
Does not regenerate any report (preserves the exact generated texts).

Usage:  python -m src.rescore            # uses the latest runs/*_ollama dir
        python -m src.rescore <run_dir>  # explicit run dir
"""
from __future__ import annotations
import glob
import json
import os
import shutil
import sys

from . import validate as val, evaluate as ev
from .pipeline import load_cfg, _p, ROOT


def run(run_dir=None):
    cfg = load_cfg()
    tol = cfg["validation"]["numeric_tolerance_rel"]
    eval_primary = cfg["experiment"].get("eval_primary", {"tag": "rag_on", "select": "median"})
    runs_root = _p(cfg, "runs_dir")
    reports_ai = os.path.join(_p(cfg, "reports_dir"), "ai")
    eval_set_dir = os.path.join(_p(cfg, "reports_dir"), "eval_set")
    os.makedirs(eval_set_dir, exist_ok=True)

    def newest_kpis(sid):
        """Newest kpis_<sid>.json across all run dirs (so a re-run of one scenario is picked up)."""
        matches = sorted(glob.glob(os.path.join(runs_root, "*", f"kpis_{sid}.json")), key=os.path.getmtime)
        return matches[-1] if matches else None

    per_scenario = {}
    eval_sel = {}
    for sc in cfg["experiment"]["scenarios"]:
        sid = sc["id"]
        kpi_path = newest_kpis(sid)
        if not kpi_path:
            continue
        print(f"[rescore] {sid}: kpis <- {os.path.relpath(kpi_path, ROOT)}")
        kpis = json.load(open(kpi_path, encoding="utf-8"))
        for tag in ("rag_on", "rag_off"):
            files = sorted(glob.glob(os.path.join(reports_ai, f"{sid}__{tag}__run*.txt")))
            if not files:
                continue
            runs = [val.validate_report(open(f, encoding="utf-8").read(), kpis, rel_tol=tol) for f in files]
            per_scenario[f"{sid}::{tag}"] = {"runs": runs, "consistency": val.consistency(runs)}
            # rebuild the blind-eval instance (median-faithfulness run), excluding AI-only S4
            if tag == eval_primary.get("tag") and not sc.get("inject_drop"):
                sel = ev.select_eval_run([r["numeric_claim_faithfulness"] for r in runs], eval_primary)
                shutil.copy(files[sel], os.path.join(eval_set_dir, f"{sid}__AI.txt"))
                eval_sel[sid] = {"tag": tag, "run": sel + 1,
                                 "faithfulness": runs[sel]["numeric_claim_faithfulness"],
                                 "rule": eval_primary.get("select", "run")}
                print(f"[eval_set] {sid}: blind-eval instance = run{sel + 1} "
                      f"(rule={eval_primary.get('select', 'run')})")

    json.dump(eval_sel, open(os.path.join(eval_set_dir, "eval_selection.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    quant = ev.aggregate_quantitative(per_scenario, eval_primary=eval_primary)
    results_dir = _p(cfg, "results_dir")
    rubric = ev.aggregate_rubric(os.path.join(results_dir, "rubric_scores.csv"),
                                 key_path=os.path.join(_p(cfg, "reports_dir"), "blind", "blind_key.csv"),
                                 cfg=cfg)
    out = os.path.join(results_dir, "summary.json")
    ev.write_summary(quant, rubric, out)
    print(f"[rescore] wrote {out}")
    print(json.dumps(quant["rq1_primary"], ensure_ascii=False, indent=2))
    return quant


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
