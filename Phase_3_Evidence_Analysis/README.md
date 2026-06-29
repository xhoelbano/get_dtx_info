# Phase 3: Evidence Analysis

Phase 3 turns the verified RCT/RWE evidence from Phase 2 into the structured
benchmarking table. One row is produced per verified study, grouped per DTx,
using the same columns as the ground-truth datasets
`Test_Datasets/test_dataset_benchmarking_numbers.csv` and
`test_dataset_benchmarking_analysis.csv`.

Output is **JSON only** for now (xlsx/csv export is deferred).

## How it works

For every verified study under
`evidence/{Country}/{slug}/verified/{RCT|RWE}/{source}/studies.json`:

1. **DTx-level columns** (name, company, ratings, ICD-10, etc.) are filled from
   `data/dtx_data.json` (Germany) or `data/dtx_data_usa.json` (USA).
2. **Study-level columns** are taken from the already-scraped study object when
   present.
3. **Gaps** in study-level columns are filled by the configured LLM, reading the
   study's raw evidence file(s) (`{study_id}.xml` / `.json` / `web_*.html`) under
   the study's `raw/` folder (falling back to the candidate `raw/` folder).
   The LLM is instructed to extract only what is present and return `""`
   otherwise (no hallucination, no web search).

Every field records its **provenance**: `dtx`, `study`, `llm`, or `empty`.

## Running

```bash
# Germany, all DTx, default provider (PHASE3_PROVIDER -> LLM_PROVIDER)
python main.py analyze-evidence --country Germany

# Small test run
python main.py analyze-evidence --country Germany --limit 2

# Benchmark a specific model (outputs go to its own folder)
python main.py analyze-evidence --provider openai --model gpt-4o

# Table from scraped JSON only (no LLM cost)
python main.py analyze-evidence --no-llm-fill
```

The Phase 3 LLM is configured independently of the rest of the pipeline via
`PHASE3_PROVIDER` / `PHASE3_MODEL` in `.env` (defaulting to `LLM_PROVIDER`).

## Output layout

Results are namespaced per model so benchmarking different models never
overwrites prior runs:

```
Phase_3_Evidence_Analysis/
  {model_slug}/
    {Country}/
      by_dtx/{slug}.json      # per-DTx rows + provenance
      phase3_combined.json    # all rows + run metadata
      run_metrics.json        # aggregated tokens / cost / latency
```

Each row carries all 29 columns as keys plus `_provenance` and `_meta`.

## Ground-truth datasets (JSON)

The manual ground-truth datasets live in `Test_Datasets/` as `;`-delimited CSV.
Convert them to Phase 3-shaped JSON (same keys, grouped per DTx) for
JSON-to-JSON comparison:

```bash
python main.py convert-testset
# -> Test_Datasets/test_dataset_benchmarking_numbers.json
# -> Test_Datasets/test_dataset_benchmarking_analysis.json
```

## Columns / schema

The table structure is data-driven from
[`data-format/phase3_analysis.json`](../data-format/phase3_analysis.json). Each
column defines its `key`, the exact CSV `label`, the `fill` strategy
(`dtx` / `study` / `study_or_llm`), the study keys to read, and an LLM
description. Edit that file to change the table without touching code.
