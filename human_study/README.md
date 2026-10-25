# Human study (RQ3)

Blind comparison of AI-generated and human-written reports. Two disjoint participant groups:
**analysts** (`A1`–`A5`) write one report per scenario S1–S3, and independent **raters** (`E1`–…)
score all 18 reports (15 human + 3 AI) on a six-dimension rubric without knowing their source.

**Ethics.** Run this only under an approved ethics protocol. Insert the link to your own approved
Information Sheet in `forms/create_analyst_google_form.gs` (`<INFO_SHEET_URL>`) and in
`config/config.yaml` (`rq3.rater_form.info_sheet_url`). All participant data are written to
`reports/` and `results/`, which are git-ignored and must never be published.

## Materials

| File | Purpose |
|---|---|
| `authoring_protocol.md` | task, information parity and inclusion rule for the analysts |
| `scenario_worksheets/` | data and questions of S1–S3, identical to the AI's KPI feed |
| `forms/create_analyst_google_form.gs` | Apps Script that builds the analysts' Google Form |
| `rubric.md` | six dimensions, anchors and protocol for the raters |

## Workflow

Run all commands from the repository root, after the main experiment has produced
`reports/eval_set/` (see the main README).

```bash
# 1) Analysts: build the form (paste the .gs into script.google.com and run it), collect responses,
#    export the response sheet as CSV, then import it (first five submissions by timestamp):
python -m human_study.import_form_responses analysts.csv --dry-run
python -m human_study.import_form_responses analysts.csv

# 2) Assemble the blind set: 18 reports, neutral IDs R01–R18 grouped by scenario, uniform
#    presentation normalisation, private key reports/blind/blind_key.csv
python -m human_study.build_blind_set

# 3) Build the raters' form script (written to reports/blind/, private: it contains the human texts)
python -m human_study.build_rater_form

# 4) Import the raters' CSV export and analyse
python -m human_study.import_rater_responses raters.csv --dry-run
python -m human_study.import_rater_responses raters.csv
python -m human_study.analyse_rq3
```

Withdrawals are honoured with `--withdraw <CODE>` in steps 1 and 4. Exclusions require a documented,
objective reason: `--exclude <CODE>="reason"`.

The analysis (`results/rq3_results.json`) reports descriptives by source, Cliff's delta with
bootstrap CIs, Krippendorff's alpha (ordinal), the rank of the AI report within each scenario and
source-guess counts. It is descriptive and exploratory.
