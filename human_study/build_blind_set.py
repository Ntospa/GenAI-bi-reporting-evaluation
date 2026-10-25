"""Assemble the RQ3 blind qualitative evaluation set (design-consistent, no fabrication).

Blind set = 15 human reports (5 analysts x S1-S3) + 3 pre-specified AI reports
(eval_set: rag_on, median run per scenario, see eval_selection.json) = 18 anonymised reports.

Ordering: reports are grouped by scenario (S1 -> R01-R06, S2 -> R07-R12, S3 -> R13-R18); the
position of each report inside its scenario is shuffled with a fixed seed. Raters see one single
fixed order.

Presentation normalisation (config: rq3.presentation_normalisation), applied identically to
every text: single paragraph, "EUR" -> "€", English entity labels -> the Greek labels shown in
the scenario data. No other word and no number is changed. Counts -> _normalisation_log.csv.

Outputs (reports/blind/ and results/):
  * <neutral_id>.txt            anonymised, presentation-normalised copies of the real texts
  * blind_key.csv               private: neutral_id, true_source, scenario, author_or_run
                                 (used only for post-hoc analysis; never shown to raters)
  * rater_assignment.csv        which neutral_ids each rater scores. Authors (A1..) and raters
                                 (E1..) are disjoint participant groups, so every rater scores the
                                 full set and no self-rating exclusion is needed.
  * rubric_scores_template.csv  the source-free sheet raters fill (report_id + 6 dims +
                                 source_guess + free-text). report_id = neutral_id.

Nothing here generates report content: it only copies, shuffles and anonymises existing files.
Human reports are collected under an approved ethics protocol; until then the set is built from whatever
exists and the missing human slots are reported (status = pending_data_collection).

Usage:  python -m human_study.build_blind_set
"""
from __future__ import annotations
import csv
import glob
import json
import os
import random
import re
import shutil

from src.pipeline import load_cfg, _p

_HUMAN = re.compile(r"^(?P<sid>.+?)__HUMAN(?:__(?P<analyst>[^.]+))?\.txt$")


def normalise_presentation(text, norm):
    """Uniform surface normalisation. Returns (text, counts). Never touches numbers."""
    counts = {"paragraphs_joined": 0, "currency_replaced": 0, "labels_replaced": 0}
    for src, dst in (norm.get("currency") or {}).items():
        text, n = re.subn(rf"\b{re.escape(src)}\b", dst, text)
        counts["currency_replaced"] += n
    # longest labels first, so "United Kingdom" is handled before any shorter overlap
    for src in sorted(norm.get("labels") or {}, key=len, reverse=True):
        text, n = re.subn(rf"\b{re.escape(src)}\b", norm["labels"][src], text)
        counts["labels_replaced"] += n
    if norm.get("single_paragraph", True):
        paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        counts["paragraphs_joined"] = max(len(paras) - 1, 0)
        text = re.sub(r"\s*\n\s*", " ", text.strip())
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text + "\n", counts


def run():
    cfg = load_cfg()
    rq3 = cfg.get("rq3", {})
    scenarios = rq3.get("human_scenarios",
                        ["S1_period_performance", "S2_region_category", "S3_trend_change"])
    dims = rq3.get("dimensions", [])
    seed = rq3.get("blind_seed", 100)
    n_raters = rq3.get("n_raters", 12)
    rater_prefix = rq3.get("rater_prefix", "E")

    reports_dir = _p(cfg, "reports_dir")
    results_dir = _p(cfg, "results_dir")
    human_dir = os.path.join(reports_dir, "human")
    eval_dir = os.path.join(reports_dir, "eval_set")
    blind_dir = os.path.join(reports_dir, "blind")
    os.makedirs(blind_dir, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)

    # selected-instance provenance (which run each eval_set AI report came from)
    sel_path = os.path.join(eval_dir, "eval_selection.json")
    eval_sel = json.load(open(sel_path, encoding="utf-8")) if os.path.exists(sel_path) else {}

    # ---- gather source items (only S1-S3; S4 is AI-only, excluded from RQ3) ----
    items = []  # (true_source, scenario, author_or_run, src_path)
    missing = []
    for sid in scenarios:
        # AI: the one pre-specified report per scenario
        ai_path = os.path.join(eval_dir, f"{sid}__AI.txt")
        if os.path.exists(ai_path):
            m = eval_sel.get(sid, {})
            label = f"{m.get('tag', 'rag_on')}/run{m.get('run', '?')}/{m.get('rule', 'median')}"
            items.append(("ai", sid, label, ai_path))
        else:
            missing.append(f"AI eval_set report missing: {sid}__AI.txt")
        # Humans: all analysts for this scenario
        humans = sorted(glob.glob(os.path.join(human_dir, f"{sid}__HUMAN*.txt")))
        if not humans:
            missing.append(f"human reports missing: {sid}__HUMAN__*.txt")
        for p in humans:
            m = _HUMAN.match(os.path.basename(p))
            analyst = (m.group("analyst") if m else None) or "A1"
            items.append(("human", sid, analyst, p))

    # ---- group by scenario, shuffle within scenario, assign neutral IDs (reproducible) ----
    rng = random.Random(seed)
    ordered = []
    for sid in scenarios:
        group = [it for it in items if it[1] == sid]
        rng.shuffle(group)
        ordered.extend(group)
    items = ordered
    norm = rq3.get("presentation_normalisation", {})
    for old in glob.glob(os.path.join(blind_dir, "R*.txt")):
        os.remove(old)
    key_rows, norm_rows = [], []
    for i, (src, sid, who, path) in enumerate(items, 1):
        nid = f"R{i:02d}"
        with open(path, encoding="utf-8") as f:
            text, counts = normalise_presentation(f.read(), norm)
        with open(os.path.join(blind_dir, f"{nid}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        key_rows.append({"neutral_id": nid, "true_source": src,
                         "scenario": sid, "author_or_run": who})
        norm_rows.append({"neutral_id": nid, **counts})
    with open(os.path.join(blind_dir, "_normalisation_log.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(norm_rows[0].keys()) if norm_rows else ["neutral_id"])
        w.writeheader(); w.writerows(norm_rows)

    # ---- private key ----
    with open(os.path.join(blind_dir, "blind_key.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["neutral_id", "true_source", "scenario", "author_or_run"])
        w.writeheader(); w.writerows(key_rows)

    # ---- rater assignment ----
    # Raters are an independent group from the authors (disjoint codes E* vs A*), so each rater
    # scores every report in the blind set: no self-rating is possible and the rating grid is
    # complete (which is what Krippendorff's alpha is happiest with).
    rater_ids = [f"{rater_prefix}{i}" for i in range(1, n_raters + 1)]
    author_ids = {r["author_or_run"] for r in key_rows if r["true_source"] == "human"}
    collision = sorted(set(rater_ids) & author_ids)
    if collision:
        raise SystemExit(
            f"[blind] ABORT: rater codes collide with analyst codes {collision}. "
            "Authors and raters must be disjoint groups; change rq3.rater_prefix in config.yaml."
        )
    with open(os.path.join(results_dir, "rater_assignment.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["rater_id", "report_id"])
        for rid in rater_ids:
            for row in key_rows:
                w.writerow([rid, row["neutral_id"]])

    # ---- rater profile template (two closed items, pseudonymous: keyed by rater code only) ----
    # Used only to describe the composition of the rater group in the dissertation. It carries no
    # identifying field, and it is never published (see .gitignore).
    with open(os.path.join(results_dir, "rater_profile_template.csv"), "w", newline="",
              encoding="utf-8") as f:
        csv.writer(f).writerow(["rater_id", "report_experience", "professional_orientation"])

    # ---- empty rubric template raters fill (source-free) ----
    tmpl_cols = (["rater_id", "report_id"] + dims + ["source_guess"]
                 + rq3.get("free_text", []))
    with open(os.path.join(results_dir, "rubric_scores_template.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(tmpl_cols)

    n_ai = sum(1 for r in key_rows if r["true_source"] == "ai")
    n_hu = sum(1 for r in key_rows if r["true_source"] == "human")
    status = "complete" if (n_ai == len(scenarios) and n_hu == rq3.get("n_analysts", 5) * len(scenarios)) \
        else "pending_data_collection"
    print(f"[blind] assembled {len(key_rows)} reports (AI={n_ai}, human={n_hu}) -> {blind_dir}")
    print(f"[blind] raters: {len(rater_ids)} independent ({rater_ids[0]}..{rater_ids[-1]}), "
          f"disjoint from authors; each rates all {len(key_rows)} reports")
    print(f"[blind] key -> blind_key.csv | assignment -> rater_assignment.csv | "
          f"template -> rubric_scores_template.csv")
    print(f"[blind] status: {status}")
    if missing:
        print("[note] pending (collect after ethics approval): " + "; ".join(missing))
    return {"status": status, "n_ai": n_ai, "n_human": n_hu, "missing": missing}


if __name__ == "__main__":
    run()
