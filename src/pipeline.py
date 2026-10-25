"""End-to-end orchestrator: data -> KPIs -> (RAG) -> generate -> validate -> evaluate.

Usage:
    python -m src.pipeline            # full run (needs Ollama)
    python -m src.pipeline --mock     # offline, faithful mock generation
    python -m src.pipeline --mock-noisy   # offline, injects an error (demo hallucination)
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import datetime as dt

import yaml

from . import data_prep, kpi_engine, generate as gen, validate as val, evaluate as ev
from . import rag_index

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def load_cfg():
    with open(os.path.join(ROOT, "config", "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _p(cfg, key):
    return os.path.join(ROOT, cfg["paths"][key])


def run(mode="ollama", only=None):
    cfg = load_cfg()
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{mode}"
    run_dir = os.path.join(_p(cfg, "runs_dir"), run_id)
    os.makedirs(run_dir, exist_ok=True)

    # 1) Data
    df = data_prep.build_dataset(cfg)
    data_hash = data_prep.snapshot(df, _p(cfg, "data_dir"))
    source_csv_hash = data_prep.source_hash(cfg)  # stable anchor (source bytes)
    print(f"[data] rows={len(df)} periods={sorted(df['period'].unique())} "
          f"source_csv_hash={source_csv_hash} snapshot_hash={data_hash}")

    N = cfg["experiment"]["runs_per_scenario"]
    base_seed = cfg["experiment"].get("base_seed", 100)
    tol = cfg["validation"]["numeric_tolerance_rel"]
    ablation = cfg["experiment"].get("ablation_rag_off", False)
    eval_primary = cfg["experiment"].get("eval_primary", {"tag": "rag_on", "run": 1})
    config_version = cfg.get("version", "unversioned")
    # prompt version = hash of the two prompt templates
    _ptxt = (gen._read("system_prompt.txt") + "\n" + gen._read("user_prompt_template.txt"))
    prompt_version = hashlib.sha256(_ptxt.encode("utf-8")).hexdigest()[:12]

    reports_dir = _p(cfg, "reports_dir")
    # safety: only the real 'ollama' mode writes to reports/ai and rebuilds eval_set. Mock modes
    # write to a mode-specific dir (reports/ai_mock*) so they can never overwrite real outputs.
    ai_subdir = "ai" if mode == "ollama" else f"ai_{mode}"
    is_real = mode == "ollama"
    os.makedirs(os.path.join(reports_dir, ai_subdir), exist_ok=True)
    os.makedirs(os.path.join(reports_dir, "eval_set"), exist_ok=True)  # 1 AI report/scenario for blind eval

    # provenance / reproducibility record (global)
    prov = {"run_id": run_id, "mode": mode, "data_hash": data_hash,
            "source_csv_hash": source_csv_hash,
            "config_version": config_version, "prompt_version": prompt_version,
            "llm": cfg["llm"], "grounding": cfg["grounding"],
            "runs_per_scenario": N, "base_seed": base_seed,
            "per_run_seeds": [base_seed + i for i in range(N)],
            "ablation": "rag_context (structured KPI feed present in both conditions)",
            "eval_primary": eval_primary}
    json.dump(prov, open(os.path.join(run_dir, "provenance.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    scenarios = cfg["experiment"]["scenarios"]
    if only:
        scenarios = [s for s in scenarios if s["id"] == only]
        print(f"[run] --only {only}: running {len(scenarios)} scenario(s)")

    per_scenario = {}
    eval_sel = {}
    for sc in scenarios:
        period = sc["period"]
        inject = sc.get("inject_drop")
        all_kpis = kpi_engine.compute_kpis(df, period, inject=inject)
        kpis = kpi_engine.select_kpis(all_kpis, sc["kpis"])
        kpi_engine.save_kpis(kpis, os.path.join(run_dir, f"kpis_{sc['id']}.json"))

        # RAG context (non-numeric) once per scenario
        query = f"{sc['title']} " + " ".join(sc["kpis"])
        context_block = rag_index.retrieve(query, cfg) if cfg["grounding"]["use_rag"] else ""

        # RAG-context ablation: 'rag_on' includes retrieved non-numeric context; 'rag_off' omits
        # it. The structured KPI feed is identical in both conditions (not grounding vs nothing).
        modes = [("rag_on", mode)]
        if ablation:
            modes.append(("rag_off", mode))

        for tag, gmode in modes:
            runs = []
            ctx = context_block if tag == "rag_on" else ""
            for i in range(N):
                seed = base_seed + i  # different per run, reproducible (RQ2 variability)
                base = f"{sc['id']}__{tag}__run{i+1}"
                report_path = os.path.join(reports_dir, ai_subdir, base + ".txt")
                # resume: reuse an existing report so a re-run continues after a timeout/crash
                # instead of re-calling Ollama (and regenerating already-completed scenarios).
                if is_real and os.path.exists(report_path) and os.path.getsize(report_path) > 0:
                    text = open(report_path, encoding="utf-8").read()
                    _, user_prompt = gen.build_prompt(sc["title"], period, kpis, ctx,
                                                      questions=sc.get("question"))
                    print(f"[resume] {base}: existing report reused (skip Ollama)")
                else:
                    out = gen.generate(sc, period, kpis, cfg, context_block=ctx, mode=gmode, seed=seed)
                    text, user_prompt = out["text"], out["user"]
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write(text)
                v = val.validate_report(text, kpis, rel_tol=tol)
                runs.append(v)
                # persist validation + per-run provenance
                run_prov = {"seed": seed, "model": cfg["llm"]["model"],
                            "temperature": cfg["llm"].get("temperature"),
                            "top_p": cfg["llm"].get("top_p"), "num_ctx": cfg["llm"].get("num_ctx"),
                            "data_hash": data_hash, "source_csv_hash": source_csv_hash,
                            "config_version": config_version,
                            "prompt_version": prompt_version, "rag": tag}
                json.dump({"validation": v, "prompt": user_prompt, "provenance": run_prov},
                          open(os.path.join(run_dir, base + ".json"), "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
            # capture the one primary AI report per scenario for blind qualitative eval, selected
            # deterministically (median-faithfulness run, ties -> lowest seed). Exclude AI-only
            # scenarios (inject_drop) which have no human counterpart. Only the real run rebuilds
            # the eval_set (mock must never touch the blind-eval artifacts).
            if is_real and tag == eval_primary.get("tag") and not sc.get("inject_drop"):
                sel = ev.select_eval_run([r["numeric_claim_faithfulness"] for r in runs], eval_primary)
                sel_base = f"{sc['id']}__{tag}__run{sel + 1}"
                shutil.copy(os.path.join(reports_dir, ai_subdir, sel_base + ".txt"),
                            os.path.join(reports_dir, "eval_set", f"{sc['id']}__AI.txt"))
                eval_sel[sc["id"]] = {"tag": tag, "run": sel + 1,
                                      "faithfulness": runs[sel]["numeric_claim_faithfulness"],
                                      "rule": eval_primary.get("select", "run")}
                print(f"[eval_set] {sc['id']}: blind-eval instance = run{sel + 1} "
                      f"(rule={eval_primary.get('select', 'run')})")
            cons = val.consistency(runs)
            per_scenario[f"{sc['id']}::{tag}"] = {"runs": runs, "consistency": cons}
            print(f"[gen] {sc['id']} {tag}: numeric_faithfulness_mean="
                  f"{sum(r['numeric_claim_faithfulness'] for r in runs)/N:.3f} "
                  f"numeric_halluc_mean={sum(r['numeric_hallucination_rate'] for r in runs)/N:.3f}")

    if is_real:
        json.dump(eval_sel, open(os.path.join(reports_dir, "eval_set", "eval_selection.json"), "w",
                  encoding="utf-8"), ensure_ascii=False, indent=2)

    # Evaluate
    quant = ev.aggregate_quantitative(per_scenario, eval_primary=eval_primary)
    results_dir = _p(cfg, "results_dir")
    rubric = ev.aggregate_rubric(os.path.join(results_dir, "rubric_scores.csv"),
                                 key_path=os.path.join(reports_dir, "blind", "blind_key.csv"),
                                 cfg=cfg)
    summary_path = os.path.join(results_dir, "summary.json")
    ev.write_summary(quant, rubric, summary_path)
    print(f"[done] summary -> {summary_path}")
    print(json.dumps(quant["rq1_primary"], ensure_ascii=False, indent=2))
    return quant


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="offline faithful mock generation")
    ap.add_argument("--mock-noisy", action="store_true", help="offline, inject an error")
    ap.add_argument("--only", default=None, help="run a single scenario id (e.g. S3_trend_change)")
    args = ap.parse_args(argv)
    mode = "mock" if args.mock else "mock-noisy" if args.mock_noisy else "ollama"
    run(mode, only=args.only)


if __name__ == "__main__":
    main()
