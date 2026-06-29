# Benchmark 1 - Study detection (counts)

Measures how completely and precisely the automated pipeline recovers the
manually curated ground-truth studies, via deterministic identifier-based record
linkage. Method and formulas: [`../methodology.md`](../methodology.md).

## Files

| File | Purpose |
|---|---|
| `run_benchmark_1.py` | The benchmark: extracts/normalizes IDs, clusters studies, matches, computes KPIs, writes results. |
| `dtx_crosswalk.json` | Maps GT DTx names to pipeline `(country, slug)` groups; flags the 7 GT DTx for which the pipeline found zero verified studies. Used for per-DTx reporting and coverage only. |
| `results/benchmark_1_results.md` | Human-readable report: headline KPIs, per-DTx table, missed studies, Extras-to-adjudicate, interpretation. |
| `results/benchmark_1_results.json` | Machine-readable KPI summary. |
| `results/<model>_matches.json` | Per-model matched / missed / extra study lists with identifiers and sources. |

## Run

```bash
python Benchmark/benchmark_1_study_counts/run_benchmark_1.py
```

## Headline (current run)

96 GT studies across 26 DTx; the pipeline returned verified studies for 19 DTx
and found zero verified evidence for the other 7 (it processed them, but no
candidate passed verification). All three models
(gpt-4o, gemini-3.1-pro-preview, claude-sonnet-4-6) yield **identical** counts
because they analyze the same Phase 2 evidence set.

| Scope | TP | FN | Extras | Recall | Precision\* | F1 |
|---|--:|--:|--:|--:|--:|--:|
| Full GT (26 DTx) | 61 | 35 | 32 | 63.5% | 65.6% | 64.6% |
| Covered subset (19 DTx) | 61 | 18 | 32 | 77.2% | 65.6% | 70.9% |

\* Precision is a corroboration rate / lower bound until the 32 **Extras** are
manually adjudicated (they may be real studies the GT missed, not necessarily
wrong hits). See the Extras section of the report.
