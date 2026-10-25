"""Import the raters' Google Forms CSV export into the long rubric format used by evaluate.py.

Canonical source: the CSV exported from the raters' form (never transcribed by hand).

Rules (no rating content is ever an inclusion criterion)
--------------------------------------------------------
  1. submissions are ordered by timestamp; a repeated withdrawal code keeps its first submission;
  2. a submission is removed only if withdrawn (--withdraw code), if the consent declarations are
     incomplete, if a required rating is missing or out of range, or if excluded by the researcher
     for an objective, documented reason (--exclude code="reason");
  3. every remaining submission is included and receives the code E1..En in timestamp order.
Uniform answering patterns (e.g. identical scores everywhere) are reported, never excluded.

Outputs (private participant data, all git-ignored)
---------------------------------------------------
  results/rubric_scores.csv      rater_id, report_id, <6 dimensions>, source_guess
  results/rater_profile.csv      rater_id, report_experience, professional_orientation
  reports/blind/_private/rater_inclusion_log.csv

Usage
-----
  python -m human_study.import_rater_responses responses.csv --dry-run
  python -m human_study.import_rater_responses responses.csv
  python -m human_study.import_rater_responses responses.csv --withdraw ROM742
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys

from src.pipeline import load_cfg, _p
from .import_form_responses import _normalise_ts, TS_FORMATS
from .build_rater_form import DIM_LABELS, STATEMENTS

GRID = re.compile(r"^Αναφορά (R\d{2}), βαθμολογία \[(.+)\]$")
GUESS = re.compile(r"^Αναφορά (R\d{2}), εκτίμηση προέλευσης")
GUESS_MAP = {"Άνθρωπος": "human", "Σύστημα τεχνητής νοημοσύνης": "ai",
             "Δεν μπορώ να κρίνω": "unsure", "": ""}
CODE_RE = re.compile(r"^[A-Z]{3}[0-9]{3}$")


def _ts(x):
    x = _normalise_ts(x)
    for fmt in TS_FORMATS:
        try:
            return dt.datetime.strptime(x, fmt)
        except ValueError:
            continue
    return None


def run(csv_path, withdraw=(), exclude=(), dry_run=False):
    cfg = load_cfg()
    dims = cfg["rq3"]["dimensions"]
    label_to_dim = {v: k for k, v in DIM_LABELS.items()}
    results_dir = _p(cfg, "results_dir")
    private_dir = os.path.join(_p(cfg, "reports_dir"), "blind", "_private")

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader)]
        rows = [r for r in reader if any(c.strip() for c in r)]

    col = {h: i for i, h in enumerate(header)}
    i_ts, i_cons = 0, col["Δηλώσεις συγκατάθεσης"]
    i_code = next(i for h, i in col.items() if h.startswith("Κωδικός απόσυρσης"))
    i_exp = col["Εμπειρία με επιχειρησιακές ή αναλυτικές αναφορές"]
    i_ori = col["Κύριος επαγγελματικός προσανατολισμός"]
    grid, guess = {}, {}
    for h, i in col.items():
        m = GRID.match(h)
        if m:
            grid[(m.group(1), label_to_dim[m.group(2)])] = i
        m = GUESS.match(h)
        if m:
            guess[m.group(1)] = i
    reports = sorted({rid for rid, _ in grid})
    if len(grid) != len(reports) * len(dims) or len(guess) != len(reports):
        sys.exit(f"ABORT: unexpected header ({len(reports)} reports, {len(grid)} rating columns).")

    parsed = [_ts(r[i_ts]) for r in rows]
    if None not in parsed:
        rows = [r for _, r in sorted(zip(parsed, rows), key=lambda t: t[0])]
    withdraw = {c.strip().upper() for c in withdraw}
    exclude = dict(e.split("=", 1) for e in exclude)
    exclude = {k.strip().upper(): v for k, v in exclude.items()}
    if any(not v.strip() for v in exclude.values()):
        sys.exit("ABORT: every --exclude needs a documented reason.")

    log, scores, profiles, seen = [], [], [], set()
    for order, r in enumerate(rows, 1):
        code = r[i_code].strip().upper()
        cons_ok = all(s in r[i_cons] for s in STATEMENTS)
        vals = {k: r[i].strip() for k, i in grid.items()}
        bad = [f"{k[0]}:{k[1]}" for k, v in vals.items() if v not in {"1", "2", "3", "4", "5"}]
        nums = [int(v) for v in vals.values() if v in {"1", "2", "3", "4", "5"}]
        e = {"order": order, "timestamp": r[i_ts].strip(), "withdrawal_code": code,
             "code_format_ok": bool(CODE_RE.match(code)), "consent_complete": cons_ok,
             "n_ratings": len(nums), "n_distinct_scores": len(set(nums)),
             "n_source_guesses": sum(1 for rid in reports if r[guess[rid]].strip()),
             "status": "", "rater_id": "", "reason": ""}
        if code in seen:
            e.update(status="duplicate", reason="repeated withdrawal code; first kept")
        elif code in withdraw:
            e.update(status="withdrawn", reason="participant withdrew")
        elif code in exclude:
            e.update(status="excluded", reason=exclude[code])
        elif not cons_ok:
            e.update(status="excluded", reason="consent declarations incomplete")
        elif bad:
            e.update(status="excluded", reason=f"missing/invalid ratings: {len(bad)}")
        else:
            rid_code = f"E{sum(1 for x in log if x['status'] == 'included') + 1}"
            e.update(status="included", rater_id=rid_code)
            for rep in reports:
                g = r[guess[rep]].strip()
                if g not in GUESS_MAP:
                    sys.exit(f"ABORT: unknown source-guess value {g!r}")
                scores.append({"rater_id": rid_code, "report_id": rep,
                               **{d: int(vals[(rep, d)]) for d in dims},
                               "source_guess": GUESS_MAP[g]})
            profiles.append({"rater_id": rid_code, "report_experience": r[i_exp].strip(),
                             "professional_orientation": r[i_ori].strip()})
        seen.add(code)
        log.append(e)

    print(f"[raters] {len(rows)} submissions, {len(reports)} reports x {len(dims)} dimensions")
    for e in log:
        flag = "  [uniform scores]" if e["n_distinct_scores"] == 1 else ""
        print(f"  #{e['order']:<2} {e['timestamp']:<26} {e['withdrawal_code']:<7} "
              f"consent={'ok' if e['consent_complete'] else 'NO'} ratings={e['n_ratings']:<3} "
              f"distinct={e['n_distinct_scores']} guesses={e['n_source_guesses']:<2} -> "
              f"{e['rater_id'] or e['status']}{'  (' + e['reason'] + ')' if e['reason'] else ''}{flag}")
    print(f"[raters] included {len(profiles)}")
    if dry_run:
        print("[raters] --dry-run: nothing written.")
        return log

    os.makedirs(private_dir, exist_ok=True)
    with open(os.path.join(results_dir, "rubric_scores.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rater_id", "report_id", *dims, "source_guess"])
        w.writeheader(); w.writerows(scores)
    with open(os.path.join(results_dir, "rater_profile.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rater_id", "report_experience", "professional_orientation"])
        w.writeheader(); w.writerows(profiles)
    with open(os.path.join(private_dir, "rater_inclusion_log.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(log[0].keys()))
        w.writeheader(); w.writerows(log)
    print(f"[raters] wrote rubric_scores.csv ({len(scores)} rows), rater_profile.csv, inclusion log")
    return log


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv_path")
    ap.add_argument("--withdraw", action="append", default=[], metavar="CODE")
    ap.add_argument("--exclude", action="append", default=[], metavar='CODE="reason"')
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, a.withdraw, a.exclude, a.dry_run)
