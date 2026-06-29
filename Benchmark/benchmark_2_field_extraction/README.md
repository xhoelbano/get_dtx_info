# Benchmark 2 - Field-level extraction quality

Measures how *correct* each model's Phase 3 analysis is, column by column, against
the manually curated ground-truth analysis
[`../../Test_Datasets/test_dataset_benchmarking_analysis.json`](../../Test_Datasets/test_dataset_benchmarking_analysis.json).
Studies are linked with the same identifier-based clustering as Benchmark 1; each
column is then scored with a data-type-appropriate metric. Method and references:
[`methodology.md`](methodology.md).

## Files

| File | Purpose |
|---|---|
| `methodology.md` | Per-column metric choices, formulas, blank handling, aggregation, the no-LLM-judge rationale, and references. |
| `column_types.json` | Schema-driven config: the metric assigned to each of the 28 columns (2 linkage fields skipped), plus the `category` synonym map. |
| `run_benchmark_2.py` | The benchmark: link/cluster/merge studies, drop unfilled/unmatched GT (logged), score every field, aggregate. |
| `results/benchmark_2_results.md` | Human-readable report: headline per model, per-column x per-model matrix, omission/hallucination, Cohen's kappa, notes. |
| `results/benchmark_2_results.json` | Machine-readable scores. |
| `results/dropped_studies.md` + `.json` | Every GT study excluded, with reason (`unfilled_gt` / `not_found_by_pipeline`). |
| `results/<model>_field_scores.json` | Per-study, per-field gold vs predicted vs score. |
| `results/outcomes_spotcheck.csv` | GT vs each model `key_outcomes_findings` for the manual negation/number review BERTScore can miss. |

## Run

```bash
# from the repository root
python Benchmark/benchmark_2_field_extraction/run_benchmark_2.py
# offline / no model download (token-level F1 instead of BERTScore for free text):
python Benchmark/benchmark_2_field_extraction/run_benchmark_2.py --no-bertscore
```

The first full run downloads the BERTScore model (`roberta-large`, ~1.4 GB) to
the Hugging Face cache.

## Headline (current run, BERTScore active)

56 GT analysis studies -> 35 evaluable (21 dropped, all `not_found_by_pipeline`:
no matching pipeline study, consistent with Benchmark 1's missed studies; 0
`unfilled_gt`).

| Model | Macro (GT-present) | Micro (GT-present) |
|---|--:|--:|
| gpt-4o | 66.2% | 69.3% |
| gemini-3.1-pro-preview | 66.6% | 69.0% |
| claude-sonnet-4-6 | 66.2% | 68.6% |

The three models are close because they analyze the same evidence; differences
show up per column (see the report). Macro-average weights every column equally
and so is pulled down by columns the pipeline rarely fills (e.g.
`company_founding_year`, store review counts) and by a genuine pipeline bug on
`diga_listing_date` (it emits the analysis date, scoring 0%). Free-text columns
score well under BERTScore (e.g. `collected_data` ~87%, `key_outcomes_findings`
~60%).
