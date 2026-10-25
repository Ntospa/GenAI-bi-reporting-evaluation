"""Import analyst responses from the Google Forms CSV export into the pipeline input format.

Why this script exists
----------------------
The Google Form is the collection instrument for the human baseline. Its response sheet, exported
as CSV, is the canonical source of the human reports because it keeps three things that manual
copying into Word loses:

  * the submission timestamp   -> needed for the pre-registered inclusion rule (first five);
  * the withdrawal code        -> chosen by the participant, needed to honour withdrawal;
  * the exact report text      -> no manual transcription, so no copying error can reach the
                                   numeric validator and be attributed to the analyst.

Inclusion rule (pre-registered before any response was received)
---------------------------------------------------------------------------
  1. submissions are ordered by timestamp;
  2. a repeated withdrawal code keeps only its first submission;
  3. a submission is removed only if withdrawn (--withdraw) or excluded by the researcher for an
     objective, documented reason (--exclude code="reason"), e.g. empty or off-topic text;
  4. the first n_analysts remaining submissions become A1..An, in timestamp order; the rest are
     reserves and move up automatically if an included submission is withdrawn or excluded.
Report quality, style or content is never an inclusion criterion.

Outputs (private participant data; git-ignored by the deny-by-default rule on reports/human/*)
-------------------------------------------------------------------------------------------
  reports/human/<scenario_id>__HUMAN__<A#>.txt     one file per included analyst x scenario
  reports/human/_private/inclusion_log.csv         full audit trail of every submission

Usage
-----
  python -m human_study.import_form_responses responses.csv --dry-run
  python -m human_study.import_form_responses responses.csv
  python -m human_study.import_form_responses responses.csv --withdraw ROM742
  python -m human_study.import_form_responses responses.csv --exclude ABC123="empty S2 answer"
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import os
import re
import sys

from src.pipeline import load_cfg, _p

TIMESTAMP_HEADERS = ("χρονική σήμανση", "χρονοσφραγίδα", "timestamp")
CODE_PREFIX = "κωδικός απόσυρσης"
REPORT_PREFIX = "η αναφορά σας"
CONSENT_PREFIX = "δηλώσεις συγκατάθεσης"
TS_FORMATS = ("%Y/%m/%d %I:%M:%S %p", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
              "%Y-%m-%d %H:%M:%S", "%d/%m/%Y, %H:%M:%S", "%d.%m.%Y %H:%M:%S")
GENERATED = re.compile(r"^.+__HUMAN__A\d+\.txt$")


def _find_columns(header):
    low = [h.strip().lower() for h in header]
    ts = next((i for i, h in enumerate(low) if h in TIMESTAMP_HEADERS), 0)
    code = next((i for i, h in enumerate(low) if h.startswith(CODE_PREFIX)), None)
    consent = next((i for i, h in enumerate(low) if h.startswith(CONSENT_PREFIX)), None)
    # The three report questions share the same title, so they are identified by position
    # (form order = scenario order). DictReader would silently collapse them; we never use it.
    reports = [i for i, h in enumerate(low) if h.startswith(REPORT_PREFIX)]
    return ts, code, consent, reports


def _normalise_ts(x):
    """Greek-locale Google Sheets export, e.g. '2026/10/07 8:02:42 μ.μ. GMT+3' -> '2026/10/07 8:02:42 PM'."""
    x = re.sub(r"\s*GMT[+-]\d+(:\d+)?\s*$", "", x.strip())
    return x.replace("π.μ.", "AM").replace("μ.μ.", "PM")


def _order_by_time(rows, ts_col):
    """Return (ordered_rows, note). Google Forms appends rows chronologically, so row order is
    the default. Timestamps are parsed only to confirm, or to repair a manually re-sorted sheet."""
    raw = [_normalise_ts(r[ts_col]) for r in rows]
    for fmt in TS_FORMATS:
        try:
            parsed = [dt.datetime.strptime(x, fmt) for x in raw]
        except ValueError:
            continue
        if parsed == sorted(parsed):
            return rows, f"timestamps parsed as {fmt}; row order is chronological"
        # parses, but sheet was re-sorted: restore true chronological order (stable)
        order = sorted(range(len(rows)), key=lambda i: parsed[i])
        return [rows[i] for i in order], f"timestamps parsed as {fmt}; rows RE-ORDERED by time"
    return rows, "WARNING: timestamps not parseable; using row order (sheet order is chronological)"


def run(csv_path, withdraw=(), exclude=(), dry_run=False):
    cfg = load_cfg()
    rq3 = cfg.get("rq3", {})
    scenarios = rq3.get("human_scenarios",
                        ["S1_period_performance", "S2_region_category", "S3_trend_change"])
    n_analysts = rq3.get("n_analysts", 5)
    human_dir = os.path.join(_p(cfg, "reports_dir"), "human")
    private_dir = os.path.join(human_dir, "_private")

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [r for r in reader if any(c.strip() for c in r)]

    ts_col, code_col, consent_col, rep_cols = _find_columns(header)
    if code_col is None:
        sys.exit("ABORT: no 'Κωδικός απόσυρσης' column found in the CSV header.")
    if len(rep_cols) != len(scenarios):
        sys.exit(f"ABORT: expected {len(scenarios)} 'Η αναφορά σας' columns, found {len(rep_cols)}.")

    rows, ts_note = _order_by_time(rows, ts_col)
    withdraw = {c.strip().upper() for c in withdraw}
    exclude = {k.strip().upper(): v for k, v in (e.split("=", 1) if "=" in e else (e, "")
                                                 for e in exclude)}
    for code, reason in exclude.items():
        if not reason.strip():
            sys.exit(f"ABORT: --exclude {code} has no reason. Exclusions must be documented.")

    log, included, seen = [], [], set()
    for order, r in enumerate(rows, 1):
        code = r[code_col].strip().upper()
        entry = {"order": order, "timestamp": r[ts_col].strip(), "withdrawal_code": code,
                 "consent_present": bool(consent_col is not None and r[consent_col].strip()),
                 "status": "", "analyst_code": "", "reason": ""}
        if code in seen:
            entry.update(status="duplicate", reason="repeated withdrawal code; first kept")
        elif code in withdraw:
            entry.update(status="withdrawn", reason="participant withdrew")
        elif code in exclude:
            entry.update(status="excluded", reason=exclude[code])
        elif not entry["consent_present"]:
            entry.update(status="excluded", reason="consent declarations missing")
        elif len(included) < n_analysts:
            entry.update(status="included", analyst_code=f"A{len(included) + 1}")
            included.append((entry["analyst_code"], [r[c] for c in rep_cols]))
        else:
            entry.update(status="reserve")
        seen.add(code)
        log.append(entry)

    print(f"[import] {len(rows)} submissions | {ts_note}")
    for e in log:
        tag = e["analyst_code"] or e["status"]
        extra = f"  ({e['reason']})" if e["reason"] else ""
        print(f"  #{e['order']:<2} {e['timestamp']:<20} {e['withdrawal_code']:<7} -> {tag}{extra}")
    print(f"[import] included {len(included)}/{n_analysts}")
    if len(included) == n_analysts:
        print("[import] REMINDER: close the form (Responses -> 'Accepting responses' OFF). "
              "Build the blind set only 4 weeks after the last included submission.")

    if dry_run:
        print("[import] --dry-run: nothing written.")
        return log

    os.makedirs(private_dir, exist_ok=True)
    # remove previously generated files so a withdrawal that promotes a reserve leaves no stale file
    for old in glob.glob(os.path.join(human_dir, "*__HUMAN__A*.txt")):
        if GENERATED.match(os.path.basename(old)):
            os.remove(old)
    for acode, texts in included:
        for sid, text in zip(scenarios, texts):
            body = text.replace("\r\n", "\n").strip() + "\n"
            with open(os.path.join(human_dir, f"{sid}__HUMAN__{acode}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(body)
    with open(os.path.join(private_dir, "inclusion_log.csv"), "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(log[0].keys()) if log else ["order"])
        w.writeheader()
        w.writerows(log)
    print(f"[import] wrote {len(included) * len(scenarios)} report files + _private/inclusion_log.csv")
    return log


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv_path", help="CSV exported from the Google Forms response sheet")
    ap.add_argument("--withdraw", action="append", default=[], metavar="CODE")
    ap.add_argument("--exclude", action="append", default=[], metavar='CODE="reason"')
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, a.withdraw, a.exclude, a.dry_run)
