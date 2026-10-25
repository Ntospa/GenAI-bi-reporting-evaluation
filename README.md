# Grounded Generative-AI Business Reporting - Evaluation Prototype

Research prototype accompanying the MSc dissertation *"Using Generative AI for the Automated
Production of Narrative Business Reports from Structured Business Intelligence Data: An Evaluation
Framework for Reliability and Decision Usefulness"* (MSc in Artificial Intelligence, University of
Essex, 2026).

The prototype generates short narrative business reports from structured sales data with a locally
hosted LLM and evaluates them for **numeric faithfulness (RQ1)**, **consistency across runs (RQ2)**
and, through a blind human study, **perceived quality against human-written reports (RQ3)**.

This repository contains code, configuration and instruments only. It contains **no outputs** of the
original study and **no participant data**. The outputs that back the dissertation are published
separately as a release (see [Verifying the dissertation results](#5-verifying-the-dissertation-results)).

---

## 1. How it works

1. **Deterministic KPI computation.** Every number is computed in Python from the dataset and stored
   as the single source of truth (`src/kpi_engine.py`).
2. **Report generation.** A local LLM (via [Ollama](https://ollama.com)) writes the report. Numbers
   come only from a structured KPI feed. Retrieval-augmented generation (RAG) supplies
   **non-numeric** context only (KPI definitions, terminology).
3. **Deterministic validation.** Every numeric claim in the text is extracted and checked against
   the computed KPIs (`src/validate.py`). The LLM never evaluates itself.

---

## 2. Setup

Requirements: Python 3.10+, [Ollama](https://ollama.com) running locally.

```bash
git clone https://github.com/Ntospa/GenAI-bi-reporting-evaluation.git
cd GenAI-bi-reporting-evaluation

python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

Quick check without Ollama (offline mock generation, verifies the whole pipeline end to end):

```bash
python -m src.pipeline --mock
```

---

## 3. Running the experiment (RQ1, RQ2)

```bash
# 1) Build the RAG index (non-numeric context only)
python -m src.rag_index

# 2) Full run: KPIs -> generation (4 scenarios x 5 runs x RAG on/off) -> validation
python -m src.pipeline

# Optional: a single scenario
python -m src.pipeline --only S3_trend_change
```

Outputs (created on the first run, not tracked by git):

| Path | Content |
|---|---|
| `reports/ai/` | the 40 generated reports |
| `reports/eval_set/` | the one representative report per scenario used in the blind study |
| `runs/<timestamp>_ollama/` | per-run provenance: seed, model, parameters, prompt and data hashes, KPIs |
| `results/summary.json` | faithfulness, hallucination rate, KPI coverage, consistency |

After any change to the validator, re-score the existing reports without regenerating them:

```bash
python -m src.rescore
```

**Validator spot-check.** Sample numeric claims for manual labelling, label the two empty columns
in `results/extractor_spotcheck.csv`, then score precision and recall:

```bash
python -m src.extractor_spotcheck --sample 8
python -m src.extractor_spotcheck --score
```

### Settings used in the study

All parameters live in `config/config.yaml`: `llama3.1:8b`, `temperature=0.2`, `top_p=0.9`,
`num_ctx=8192`, `num_predict=700`, per-run seeds `100–104`, relative numeric tolerance 1%.

| Scenario | Role |
|---|---|
| **S1** Period performance | comparison scenario (RQ1–RQ3) |
| **S2** Revenue by region / category | comparison scenario (RQ1–RQ3) |
| **S3** Trend analysis (Nov → Dec 2013) | comparison scenario (RQ1–RQ3) |
| **S4** Controlled ground truth | AI-only robustness probe: a known −40% change is injected into one category |

The RAG ablation keeps the structured KPI feed identical in both conditions, so it measures the
contribution of the retrieved context, not "grounding vs no grounding".

---

## 4. Human study (RQ3, optional)

`human_study/` contains the instruments and scripts for the blind comparison with human analysts:
the authoring protocol, the scenario worksheets, the rubric, the Apps Script that builds the
analysts' Google Form, and the scripts that import responses, assemble the blind set, build the
raters' form and analyse the ratings. See `human_study/README.md`.

Running it requires recruiting participants under an **approved ethics protocol** with your own
Information Sheet and consent procedure. All participant data are written to `reports/` and
`results/`, which are git-ignored.

---

## 5. Verifying the dissertation results

The outputs of the original study (the 40 generated reports, the published run provenance,
`summary.json`, the validator spot-check labels and the aggregated RQ3 results) are attached to the
release **[thesis-results](https://github.com/Ntospa/GenAI-bi-reporting-evaluation/releases/tag/thesis-results)**.
To recompute the reported RQ1/RQ2 numbers from those reports with this code:

```bash
# from a fresh clone, extract thesis-results.zip into the repository root, then:
python -m src.rescore
```

This reproduces `results/summary.json` exactly (overall faithfulness 0.931) and selects the same
blind-evaluation reports. No Ollama is needed for this step.

**Reference results (S1–S3, `rag_on`, 15 runs)**

| Scenario | Faithfulness | Hallucination rate | KPI coverage |
|---|---:|---:|---:|
| S1 | 0.923 | 0.077 | 0.867 |
| S2 | 1.000 | 0.000 | 0.815 |
| S3 | 0.869 | 0.131 | 0.644 |
| **Overall** | **0.931** | **0.069** | **0.776** |

S4 robustness probe: 0.818 (`rag_on`) vs 0.642 (`rag_off`). RQ3 (12 raters): the AI reports were
rated lower than the human reports on all six rubric dimensions (Cliff's δ from −0.22 to −0.40),
with low inter-rater agreement.

---

## 6. Data

- Source: Microsoft **AdventureWorks** sample database (MIT), table `FactInternetSales`.
- `data/adventureworks_internetsales_canonical.csv`: 60,398 rows, 2010-12 → 2014-01.
- Stable content hash (`source_csv_hash`): **`b55cbddf5950`**. Reporting period 2013-12,
  comparison period 2013-11.

---

## 7. Notes and limitations

- **Bit-identical reproduction is not claimed.** A different Ollama version, model build or
  hardware can produce different text even with the same seed. Re-running the pipeline gives a
  comparable experiment, not the same reports. Use the release to verify the reported numbers.
- The metric is **numeric-only**: it checks the fidelity of numbers, not semantic correctness.
- Exploratory, single-model case study with three comparison scenarios. No inferential or causal
  claims are made.

---

## 8. Layout

```
config/         config.yaml (all run parameters)
data/           canonical dataset
prompts/        system and user prompt templates
context_docs/   non-numeric material for RAG
src/            pipeline: data_prep, kpi_engine, rag_index, generate, validate,
                evaluate, pipeline, rescore, extractor_spotcheck
human_study/    RQ3 instruments and scripts
```

## 9. License and citation

Code released under the **MIT License** (`LICENSE`). Data derived from AdventureWorks (MIT).

> Pantos, S. (2026). *Using Generative AI for the Automated Production of Narrative Business Reports
> from Structured Business Intelligence Data: An Evaluation Framework for Reliability and Decision
> Usefulness.* MSc dissertation, University of Essex.
