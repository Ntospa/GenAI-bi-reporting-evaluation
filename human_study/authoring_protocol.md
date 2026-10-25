# Human Reference Reports - Authoring Protocol

Defines how the **human baseline** reports are produced, so that they are comparable to the AI
reports and valid for the AI-vs-human comparison (RQ1 applied to humans, and RQ3).

## 1. Integrity rule

Human reference reports **must be written by humans**, not generated or paraphrased by an LLM.
Otherwise the "AI vs human" comparison would collapse into "AI vs AI". They are written by **five
independent analysts**; the researcher does not author any of them.

## 2. Same information base as the AI

For each scenario the analysts receive **exactly the same figures** the AI receives in its
structured KPI feed (see `scenario_worksheets/<scenario>.md`, computed from the canonical CSV,
`source_csv_hash b55cbddf5950`). No other data is provided. The interactive Power BI dashboard is
**not** an analyst input: it would expose more than the AI sees (other months, drill-downs) and
break information parity. Analysts and the AI also share the same non-numeric context: a short
KPI glossary identical to the AI's RAG source `context_docs/kpi_definitions.md`.

## 3. Task

- **Three analytical questions per scenario**, answered in a **single flowing report of
  ~150–250 words** (completeness and clarity over word count; no headings or lists).
- **Reader:** a business manager who needs a quick, reliable picture for decision-making.
- **Data rule:** use the provided values as they are; simple directly-derived calculations
  (absolute differences, percentage changes, shares) are allowed; no external data, estimates or
  unsupported assumptions.
- **Observation vs interpretation:** values and computed changes must be grounded in the data;
  any explanation is stated as a hypothesis.
- **Style:** professional, natural Greek prose; quantities in plain words, not KPI keys.
- The **identical questions and data rules** are given to the AI, with no persona or style
  add-ons.

## 4. Scenarios (period 2013-12)

| Scenario | Focus | Worksheet |
|---|---|---|
| S1_period_performance | Overall period performance, incl. November total | `scenario_worksheets/S1_period_performance.md` |
| S2_region_category | Revenue by region and category, with total as share base | `scenario_worksheets/S2_region_category.md` |
| S3_trend_change | November → December change, total and by category | `scenario_worksheets/S3_trend_change.md` |

**S4_synthetic_groundtruth** is an AI-only robustness scenario and is not part of the human task.

## 5. Participants and collection

- **Instrument:** a Google Form, built by `forms/create_analyst_google_form.gs`, that contains the
  full task material (consent declarations, instructions, glossary, scenario data and questions).
- **Consent:** given electronically through eight consent declarations at the start of the form
  (content of the approved institutional consent form); the Information Sheet is linked from the
  form.
- **Anonymity:** no email address recorded, no sign-in required. Each participant chooses a
  **withdrawal code** (`AAA999`) that does not identify them; withdrawal is possible for four
  weeks after submission.
- **Inclusion rule (pre-registered):** invitations are sent to more than five candidates; the
  **first five submissions by timestamp** form the human comparison pool. Removal only for a
  documented objective reason or on withdrawal, in which case the next submission moves up.
  Report quality, style or content is **never** a criterion. The form is closed after the fifth
  valid submission.

## 6. Import and storage

1. Export the form's response sheet as CSV.
2. `python -m human_study.import_form_responses <file.csv> --dry-run`, then without `--dry-run`.
3. The script assigns the processing codes `A1`–`A5` by timestamp and writes
   `reports/human/<scenario>__HUMAN__<A#>.txt` (the naming expected by `build_blind_set.py`) plus `reports/human/_private/inclusion_log.csv`.

**Do not transcribe responses by hand**: manual copying loses the timestamp and the withdrawal code
and can introduce copying errors into the texts that the raters evaluate. All files
produced here are participant data and are git-ignored.

## 7. Comparability and fairness controls

- Identical data and questions for humans and AI; differences reflect narration quality, not
  inputs.
- Matched length band (~150–250 words).
- Factual accuracy of human and AI reports is compared by blind raters (RQ3). The automatic
  numeric validator is applied to AI reports only.
- Blind evaluation by an **independent group of raters** (`E1`–`E12`) who did not author any
  report, using the six-dimension rubric (`rubric.md`).

## 8. Limitations

Five analysts cannot represent the full range of professional practice, and the authoring
conditions may introduce bias. The human baseline is a controlled comparison pool that captures
between-analyst variability, not a definitive industry benchmark.
