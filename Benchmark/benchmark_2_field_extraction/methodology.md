# Benchmark 2 methodology - Field-level extraction quality

This benchmark answers a different question from Benchmark 1. Benchmark 1 asked
*"did the pipeline find the right studies?"* (evidence retrieval). Benchmark 2
asks *"for the studies it found, did it extract the correct value in each
column?"* (extraction accuracy), scored cell-by-cell against the manually curated
ground truth (GT) in
[`../../Test_Datasets/test_dataset_benchmarking_analysis.json`](../../Test_Datasets/test_dataset_benchmarking_analysis.json).

Full citations are in [`../references.md`](../references.md).

## 1. Core principle

A single metric must not be used for the whole table. Correctness is
**field-dependent**: identifiers need exact match, numbers need tolerance, names
need normalization, categorical labels need chance-corrected agreement, lists
need set overlap, and free text needs semantic comparison. Each column is scored
with the metric that fits its data type and then aggregated to one number per
model, while the full per-column table is kept so every field can be discussed
individually. This schema-driven, per-field approach follows modern
structured-extraction benchmarks (ExtractBench; ContextualAI/extract-bench;
FAIRmat extract-eval; RealDocBench), which declare a metric per field rather than
scoring a whole record with one rule. The per-field metric assignment lives in
[`column_types.json`](column_types.json).

## 2. Study linkage and merging (shared with Benchmark 1)

The GT analysis subset and each model's Phase 3 output both list studies with a
`trial_registration_id` and a `sources_publications` cell. The *same* study can
appear as several records (a registered protocol plus its later publication).
Before any field can be compared, records of the same study must be collapsed:

1. **Identifier extraction / normalization** and **union-find clustering** are
   reused verbatim from Benchmark 1 (`normalize_ids`, `cluster_records` in
   [`../benchmark_1_study_counts/run_benchmark_1.py`](../benchmark_1_study_counts/run_benchmark_1.py)):
   records sharing a normalized identifier (NCT / DRKS / ISRCTN / EUCTR / PMID /
   PMC / DOI) form one study cluster.
2. **Field merge within a cluster.** A cluster is reduced to one record per
   field by taking the informative non-empty value. This implements the
   protocol-then-results case: if `key_outcomes_findings` is empty in the
   protocol record but present in the results record, the merged study carries
   the result. Values are never concatenated or invented; when two records both
   carry a value they describe the same study and the populated one is kept.

GT and pipeline studies are then **matched** when their identifier sets intersect
(exact-key linkage; symmetric and deterministic). Because all three models
analyze the identical Phase 2 evidence, the matched study set is the same across
models; only the field *values* differ - which is exactly what Benchmark 2
measures.

## 3. Which studies are evaluated

The evaluable set is the intersection, with two documented exclusions:

- **`unfilled_gt`** - a GT study whose study-level analysis fields are all empty
  (it was listed but not manually analyzed) is dropped: there is no gold value to
  score against.
- **`not_found_by_pipeline`** - a GT study with no matching pipeline study is
  dropped: there is no model output to score.

Every excluded study is logged with its reason in
[`results/dropped_studies.md`](results/dropped_studies.md). Only studies present
*and analyzed* in the GT *and* found by the pipeline are scored, giving a clean,
fair field comparison.

## 4. Per-column metrics

The two linkage fields (`trial_registration_id`, `sources_publications`) are
excluded from field scoring (they were evaluated in Benchmark 1). The remaining
26 columns are grouped as follows.

### Group A - Categorical / binary
`is_it_a_diga`, `diga_listing_status`, `risk_class`, `evidence_type`, `category`.

- **Metric:** normalized exact-match **accuracy** plus **Cohen's kappa**
  (chance-corrected agreement). Both are reported because kappa can look low on a
  skewed field even when accuracy is high (the kappa paradox), e.g. almost every
  `is_it_a_diga` is "YES".
- **Normalization is field-specific** and grounded in the observed data:
  - `diga_listing_status`: case-folded, so "Permanently Listed" == "Permanently
    listed" and "removed (09.09.2025)" == "Removed (09.09.2025)".
  - `risk_class`: the class token is extracted, so GT "I" == model "Risk Class I
    according to MDR", and "IIa" == "Risk Class IIa according to MDR"; FDA classes
    are matched on their roman numeral too.
  - `category`: GT and model use different vocabularies (e.g. GT "Psyche" vs
    model "Mental Health"; GT "Muscles, bones and joints" vs model
    "Musculoskeletal"). Each cell is split into concept tokens, each token mapped
    to a canonical concept via the editable `category_synonyms` map in
    [`column_types.json`](column_types.json), and scored as **set overlap (F1)**
    of canonical concepts. A strict normalized-exact score is also reported as a
    lower bound. This is an explicit heuristic (see Limitations).
- **Papers:** Cohen (1960); Artstein & Poesio (2008).

### Group B - Short string / name
`dtx_name`, `company_provider`.

- **Metric:** **normalized exact match** (lowercase, strip legal suffixes
  GmbH/AG/Inc/Ltd/etc., strip punctuation) with **Levenshtein ratio** as a
  graded fallback (reported, threshold ~0.85 for the pass/fail view). "GAIA" vs
  "Gaia GmbH" is the correct answer written differently and must not be a miss.
  This is a string-similarity problem, not free text. (extract-bench
  `string_exact` / `string_fuzzy`.)

### Group C - Numeric
Integers / counts (`company_founding_year`, `study_size_participants`,
`trial_arms`, `primary_end_point_duration_weeks`, `reviews_on_playstore`,
`reviews_on_appstore`): **exact match**. Continuous / rounded values
(`rating_on_playstore`, `rating_on_appstore`): **+/-5% tolerance** so 4.3 vs 4.31
is not a miss. Normalized error is reported on the misses to describe *how* wrong.
(extract-bench `integer_exact` / `number_tolerance`.)

### Group D - Dates
`diga_listing_date`, `trial_start_date`, `trial_end_date`, `publication_date`.

- **Metric:** parse both sides to `(year, month, day)` and compare at the
  **granularity present in the GT** (if the GT gives only "March 2022", compare
  year+month; if only a year, compare year). Many formats appear ("March 2022",
  "01 September 2024", "2026-04-14", "3 months"); a value that does not parse as a
  date counts as a mismatch. Date normalization avoids false misses caused purely
  by format.

### Group E - Set / list
`clinical_area_icd10`.

- **Metric:** extract ICD-10 codes by regex and compute **set
  Precision/Recall/F1 + Jaccard**, in two variants: **strict** (full code, e.g.
  `F90.0`) and **relaxed** (3-character category prefix, e.g. `F90`). Set metrics
  reward partial correctness (3 of 4 codes); the strict/relaxed split mirrors the
  exact-vs-type distinction from NER evaluation (Batista 2018; MUC/SemEval).
  Jaccard (1912) summarizes overlap.

### Group F - Long free text (genuinely unstructured)
`key_outcomes_findings`, `intervention_and_control_group`,
`additional_info_about_trial_arms`, `duration_additional_info`, `collected_data`.

- **Metric:** **BERTScore-F1** (primary) with **token-level F1, SQuAD-style**
  (lexical cross-check). These fields are paraphrase-heavy - "symptoms decreased
  significantly" vs "significant symptom reduction" mean the same thing - so exact
  match and n-gram metrics (ROUGE/BLEU) wrongly punish correct paraphrase.
  BERTScore compares meaning via contextual embeddings, is deterministic and
  reproducible, and needs no model opinion.
- **Papers:** Zhang et al. (2020, ICLR) - BERTScore; Rajpurkar et al. (2016) -
  token-level F1. ROUGE (Lin 2004) and BLEU (Papineni et al. 2002) are cited only
  to explain why they were rejected here.

### Group G - Short mixed
`follow_up_after_primary_end_point` (values like "24" weeks, "yes", "3 months").

- **Metric:** normalized exact match with a token-level F1 fallback - the field is
  short and inconsistently typed, so a graded lexical score is the fair choice.

## 5. Why not an LLM judge (design divergence)

The structured-extraction frameworks cited above use an **LLM judge** for
free-text fields. Benchmark 2 deliberately does **not**. The systems under
evaluation are GPT-4o, Claude and Gemini; using one of them (or a sibling) to
grade their own/peers' free text introduces **self-enhancement and circularity
bias** and is non-deterministic. BERTScore is the fair, reproducible middle path:
it credits paraphrase like a judge would, but is a fixed function of the two
strings and a frozen embedding model. The trade-off (BERTScore can be lenient on
negation and exact numbers) is mitigated by a manual spot-check (Section 7).

## 6. Blank handling and aggregation

For every (study, column) the cell is classified:

- **both empty** -> correct (score 1), but also tracked separately and reported as
  a "non-trivial" score that excludes both-empty cells, so sparse columns do not
  inflate the headline.
- **GT has a value, model empty** -> **omission**.
- **GT empty, model filled** -> **hallucination**.

Omission and hallucination rates are reported per column, because they are
different failure modes with different implications.

Aggregation:

- Every column metric is normalized to `[0, 1]`.
- **Macro-average across columns** is the headline per-model number (every field
  counts equally, suiting a column-by-column discussion). **Micro-average**
  (weighted by number of scored cells) is reported as a secondary figure.
- The full per-column x per-model table is always kept alongside the aggregate.

Formulas (sets, per column over studies):
`Precision = |G ∩ P| / |P|`, `Recall = |G ∩ P| / |G|`,
`F1 = 2PR/(P+R)`, `Jaccard = |G ∩ P| / |G ∪ P|`.
Cohen's kappa: `κ = (p_o - p_e) / (1 - p_e)` with observed agreement `p_o` and
chance agreement `p_e`.

## 7. Limitations and threats to validity

- **GT completeness / correctness.** Scores are relative to a manual reference
  that may contain its own gaps or errors; a low field score can reflect GT, not
  only the model.
- **`category` vocabulary mismatch.** GT and models use different category
  vocabularies; the canonical-concept map is an explicit heuristic. The
  concept-set score is the meaningful figure and the strict-exact score is a
  lower bound; both are reported.
- **BERTScore on negation/numbers.** BERTScore may rate "symptoms increased" and
  "symptoms decreased" as similar. Mitigation: a ~10% manual spot-check of
  `key_outcomes_findings` is exported to
  [`results/outcomes_spotcheck.csv`](results/outcomes_spotcheck.csv).
- **Date granularity rule.** Comparing at the GT's granularity rewards correct
  coarse answers and will not penalize a model for omitting a day the GT also
  lacks; this is intentional but should be stated.
- **Small evaluable set.** The analysis GT is a deliberately small random subset;
  per-column figures are indicative, not population estimates.
