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

- **Metric:** parse both sides to `(year, month, day)` and compare **exactly** at
  the **granularity present in the GT** (if the GT gives only "March 2022", compare
  year+month; if only a year, compare year). The parser accepts every format that
  occurs in the data - ISO ("2026-04-14"), German "dd.mm.yyyy", bare year, and
  English month names in either order and with optional commas or ordinal suffixes
  ("15 December 2020", "December 15, 2020", "February, 2021", "March 2022") - so a
  correct date written in a different style is never a false miss. There is **no
  partial credit**: a prediction *coarser* than the GT (e.g. "2021" when the GT
  records "20 April 2021") scores 0, and a value that does not parse as a date is a
  mismatch. Rewarding coarser predictions was deliberately rejected as inflating
  the score; the resulting behaviour is documented as a metric limitation rather
  than smoothed over.

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

### Group G - Follow-up duration
`follow_up_after_primary_end_point` (values like "24" weeks, "24 & 48", "yes",
"3 months", "6 months and 1 year").

- **Metric:** **exact match after unit normalization to weeks**
  (`duration_weeks_exact`). Both sides are converted to a set of whole-week
  values using a fixed **1 month = 4 weeks** (and **1 year = 48 weeks**)
  conversion: a bare number is weeks (the GT convention, e.g. "24" or "24 & 48"),
  "n week(s)" is `n`, "n month(s)" is `4n`, "n year(s)" is `48n`; connectors
  (`&`, `and`, `,`) produce multiple values, and when a string mixes bare and
  explicit numbers ("6 and 12 months") the bare numbers inherit the last explicit
  unit. The two sets must be **equal** - there is no tolerance band and no partial
  credit. This removes only the pure unit mismatch (a model answering "6 months"
  when the GT says 24 weeks is now correct, `6 x 4 = 24`); a genuinely different
  duration ("3 months" vs 24 weeks) or a contradictory answer still scores 0. A
  non-numeric GT (e.g. "yes") falls back to normalized exact match. Earlier this
  field used a lexical token-F1 that scored correct-but-differently-phrased
  durations near zero; the unit-aware exact metric is both fairer and stricter.

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

- **both empty** -> the model correctly agreed there was nothing to report. Tracked
  as a count only; **not scored** (it is neither a success nor a failure of
  extraction and would otherwise inflate sparse columns).
- **GT has a value, model empty** -> **omission**. A genuine miss; scored as 0 and
  included in the score population.
- **GT empty, model filled** -> **unverified addition** (not "hallucination").
  This is the key correction over the earlier version of this benchmark. The
  analysis GT is a deliberately small, manually curated subset and is known to be
  **incomplete** (Section 7); a value the model supplies where the annotator left
  a blank is therefore a *candidate* that would need a source check, not an
  automatic error. Calling it a "hallucination" and scoring it 0 would (a)
  penalize the model for the GT's gaps and (b) contradict how Benchmark 1 treats
  the symmetric case, where pipeline studies with no GT counterpart are reported
  as **Extras needing adjudication, not false positives**. Additions are therefore
  **counted, never scored**, and are reported per column as counts.

Only cells where the GT has a value ("GT-present" = omission + scored) enter the
score. Additions and both-empty cells are excluded from every score.

**Reporting omissions and additions.** Omission is a true failure mode, so an
omission *rate* over GT-present cells is meaningful and is reported. An addition
*rate* over GT-blank cells is only reported when there are enough GT-blank cells
to make a percentage informative (at least ten in this study, matching the
`MIN_RATE_DENOM` guard in `run_benchmark_2.py`); below that threshold a "100%"
would be computed over one or two cells and is noise, so only the raw count is
shown. Additions still require manual adjudication before they can be split into
true hallucinations versus correct values the GT simply omitted.

Aggregation:

- Every column metric is normalized to `[0, 1]`.
- **Macro-average across columns** (of the GT-present score) is the headline
  per-model number (every field counts equally, suiting a column-by-column
  discussion). **Micro-average** (weighted by the number of GT-present cells) is
  reported as a secondary figure. Because additions and both-empty cells are
  excluded, this headline is unaffected by the correction above.
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
- **Date granularity rule.** Comparison is exact at the GT's granularity. A
  prediction *coarser* than the GT (a year where the GT has a full day) scores 0
  even though the year is right; partial credit was rejected to avoid inflating
  the score, so this understates models that return correct-but-coarser dates.
- **App-store review-count volatility.** `reviews_on_playstore` /
  `reviews_on_appstore` are live counts that change continuously; the GT and the
  pipeline captured them at different times, so exact-integer scoring understates
  this field. A tolerance band was deliberately not added (it would be arbitrary),
  so the reported score is a lower bound, not a true error rate.
- **`diga_listing_date` is a deterministic pipeline field.** It is read from the
  DiGA directory ("Erstmalige Aufnahme in das DiGA-Verzeichnis") and passed
  through identically to all three models, so its per-model scores are equal by
  construction; it measures the directory scrape, not model reasoning.
- **Small evaluable set.** The analysis GT is a deliberately small random subset;
  per-column figures are indicative, not population estimates.
