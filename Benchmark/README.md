# Benchmark

This folder holds everything used to scientifically benchmark the automated DTx
evidence pipeline for the thesis: the ideas, plans, scientific references,
formulas, code, and generated results. Nothing here changes the pipeline; it
only reads pipeline outputs and the manually curated ground truth and produces
reproducible metrics.

## Contents

| File / folder | Purpose |
|---|---|
| [`methodology.md`](methodology.md) | The record-linkage method shared across benchmarks: identifier normalization, clustering, KPI formulas, and the rationale for deterministic (non-LLM) matching. |
| [`references.md`](references.md) | Full scientific citation list referenced by both benchmarks. |
| [`benchmark_1_study_counts/`](benchmark_1_study_counts/) | Benchmark 1: study-detection performance of the pipeline vs. the ground-truth study list. Contains the code, the DTx crosswalk, and generated results. |
| [`benchmark_2_field_extraction/`](benchmark_2_field_extraction/) | Benchmark 2: field-level extraction quality of each model's Phase 3 analysis vs. the manual ground-truth analysis. Contains the per-column metric methodology, the schema-driven metric config, the code, and generated results. |

## Benchmarks

1. **Benchmark 1 - Study detection (counts).** How well the automated
   extraction + classification pipeline recovers the studies that were curated
   manually in [`../Test_Datasets/test_dataset_benchmarking_numbers.json`](../Test_Datasets/test_dataset_benchmarking_numbers.json).
   Implemented and documented under
   [`benchmark_1_study_counts/`](benchmark_1_study_counts/).
2. **Benchmark 2 - Field-level extraction quality.** Given the studies the
   pipeline found, how *correct* are the extracted values per column, compared
   cell-by-cell against the manual ground-truth analysis in
   [`../Test_Datasets/test_dataset_benchmarking_analysis.json`](../Test_Datasets/test_dataset_benchmarking_analysis.json).
   Each column is scored with a data-type-appropriate metric (exact / set /
   tolerance / BERTScore) and aggregated per model. Implemented and documented
   under [`benchmark_2_field_extraction/`](benchmark_2_field_extraction/).

## What is being benchmarked (Benchmark 1)

The evidence candidates were extracted and classified once, by the Phase 2
pipeline using **gpt-4o with Website Search / Browser Use ON** (the
[`../evidence`](../evidence) folder). Phase 3 then analyzed that *same* evidence
with three different models. For the **counts** benchmark we therefore use the
Phase 3 combined files purely as a convenient carrier of the per-study
identifiers (`trial_registration_id`, `sources_publications`); because all three
models analyze the identical evidence set, the *study set* they expose is the
same. Running all three is a built-in determinism check, not three different
extractions.

Pipeline outputs compared:

- `../Phase_3_Evidence_Analysis/gpt-4o/Website_Search_ON/phase3_combined.json`
- `../Phase_3_Evidence_Analysis/gemini-3.1-pro-preview/Website_Search_ON/phase3_combined.json`
- `../Phase_3_Evidence_Analysis/claude-sonnet-4-6/Website_Search_ON/phase3_combined.json`

## How to run

```bash
# from the repository root
python Benchmark/benchmark_1_study_counts/run_benchmark_1.py
python Benchmark/benchmark_2_field_extraction/run_benchmark_2.py
```

These regenerate everything under each benchmark's `results/` folder.
