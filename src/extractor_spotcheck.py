"""Manual spot-check harness for the numeric-claim extractor/matcher (RQ1 validity).

The deterministic validator (validate.py) extracts numbers from a report and matches each to the
locked KPIs (raw values + Option-A derived values). Extraction/matching is imperfect, so its
precision/recall must be estimated by manual annotation before RQ1 numbers are reported.

Two steps:
  1) python -m src.extractor_spotcheck --sample 8
     Samples reports (stratified across scenarios), dumps every extracted numeric claim with the
     validator's decision (matched? raw/derived? which KPI value?) and a short text context, into
     results/extractor_spotcheck.csv, with two empty columns for a human to label.
  2) A human labels each row:
       is_true_number  = 1 if the token is a genuine numeric claim in the text, 0 if noise
                         (a code, list index, stray digit the extractor should have ignored)
       should_match    = 1 if it should be credited against a KPI/derived value, else 0
     Then: python -m src.extractor_spotcheck --score
     computes matcher precision/recall and extraction-noise rate from the labelled file.

Also read each sampled report once and add rows (report, extracted_value blank, ...) for any true
numeric claim the extractor missed entirely (is_true_number=1, should_match=1, validator_matched=0)
so recall reflects missed extractions too.

Nothing here fabricates results: it only prepares/aggregates a manual annotation. The labels must be
supplied by the researcher (human), not by the model.
"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import random

from . import data_prep, kpi_engine, validate as val
from .pipeline import load_cfg, _p

CSV_COLS = ["report", "context", "extracted_raw", "candidates", "unit",
            "validator_matched", "matched_kind", "matched_value",
            "is_true_number", "should_match"]  # last two = manual labels (leave blank)


def _kpis_for(cfg, df, sid):
    scen = {s["id"]: s for s in cfg["experiment"]["scenarios"]}
    sc = scen[sid]
    allk = kpi_engine.compute_kpis(df, sc["period"], inject=sc.get("inject_drop"))
    return kpi_engine.select_kpis(allk, sc["kpis"])


def _sample_files(rep_dir, n, seed=0):
    """Stratified, deterministic sample across scenarios (AI rag_on + human if present)."""
    ai = glob.glob(os.path.join(rep_dir, "ai", "*__rag_on__*.txt"))
    hu = glob.glob(os.path.join(rep_dir, "human", "*__HUMAN*.txt"))
    files = ai + hu
    by_sid = {}
    for f in files:
        sid = os.path.basename(f).split("__")[0]
        by_sid.setdefault(sid, []).append(f)
    rng = random.Random(seed)
    picked, sids = [], sorted(by_sid)
    i = 0
    while len(picked) < min(n, len(files)):
        sid = sids[i % len(sids)]
        pool = [f for f in by_sid[sid] if f not in picked]
        if pool:
            picked.append(rng.choice(pool))
        i += 1
        if i > n * len(sids) + 10:
            break
    return sorted(picked)


def sample(n):
    cfg = load_cfg(); df = data_prep.build_dataset(cfg)
    tol = cfg["validation"]["numeric_tolerance_rel"]
    rep_dir = _p(cfg, "reports_dir")
    files = _sample_files(rep_dir, n)
    if not files:
        print("[spotcheck] no reports found (run the pipeline first)."); return
    rows = []
    for f in files:
        base = os.path.basename(f)
        sid = base.split("__")[0]
        try:
            kpis = _kpis_for(cfg, df, sid)
        except KeyError:
            continue
        acc = val._acceptable_values(kpis)
        text = val._strip_product_names(open(f, encoding="utf-8").read(), kpis)
        for cands, unit, raw, context in val._iter_claims(text):
            hit = val._matches((cands, unit), acc, tol)
            matched = hit is not None
            rows.append([base, context, raw, ";".join(str(round(c, 2)) for c in sorted(cands)),
                         unit or "", int(matched),
                         hit[1] if hit else "", (round(hit[0], 2) if hit else ""),
                         "", ""])  # manual labels blank
    out = os.path.join(_p(cfg, "results_dir"), "extractor_spotcheck.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig: Excel-friendly Greek
        w = csv.writer(fh); w.writerow(CSV_COLS); w.writerows(rows)
    print(f"[spotcheck] {len(rows)} extracted claims from {len(files)} reports -> {out}")
    print("Reports sampled: " + ", ".join(os.path.basename(f) for f in files))
    print("Now LABEL 'is_true_number' and 'should_match' (1/0) in the CSV, add rows for any MISSED "
          "true numbers, then run: python -m src.extractor_spotcheck --score")


def score():
    cfg = load_cfg()
    path = os.path.join(_p(cfg, "results_dir"), "extractor_spotcheck.csv")
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8-sig"))
            if r.get("is_true_number") not in (None, "")]
    if not rows:
        print("No labelled rows yet. Label is_true_number/should_match first."); return
    # matcher precision/recall (over labelled claims)
    tp = sum(1 for r in rows if r["validator_matched"] == "1" and r["should_match"] == "1")
    fp = sum(1 for r in rows if r["validator_matched"] == "1" and r["should_match"] == "0")
    fn = sum(1 for r in rows if r["validator_matched"] == "0" and r["should_match"] == "1")
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    # extraction noise: tokens extracted that are not true numeric claims
    noise = sum(1 for r in rows if r["is_true_number"] == "0")
    print(f"[spotcheck] labelled={len(rows)}  TP={tp} FP={fp} FN={fn}")
    print(f"[spotcheck] matcher precision={prec}  recall={rec}  f1={f1}")
    print(f"[spotcheck] extraction-noise tokens (is_true_number=0)={noise} "
          f"({noise/len(rows):.1%} of labelled)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    score() if a.score else sample(a.sample)
