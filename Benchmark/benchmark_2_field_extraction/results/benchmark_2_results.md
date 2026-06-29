# Benchmark 2 - Field-level extraction quality (results)

_Generated: 2026-06-29T18:47:48+00:00_

Per-field accuracy of each model's Phase 3 analysis vs. the manual ground truth, scored with data-type-appropriate metrics (see [`../methodology.md`](../methodology.md)).

## Dataset

- GT studies (after clustering): **56**.
- Evaluable (filled GT and found by pipeline): **35**.
- Dropped: **21** - 0 `unfilled_gt`, 21 `not_found_by_pipeline` (see [`dropped_studies.md`](dropped_studies.md)).
- BERTScore active: **True** (roberta-large).

## Headline (per model)

Macro-average over columns of the per-column score on cells where the GT has a value (omissions count as 0; trivially-correct both-empty cells excluded). Micro-average weights by number of such cells.

| Model | Macro (GT-present) | Micro (GT-present) | Macro (incl. both-empty) |
|---|--:|--:|--:|
| gpt-4o | 66.2% | 69.3% | 69.5% |
| gemini-3.1-pro-preview | 66.6% | 69.0% | 68.0% |
| claude-sonnet-4-6 | 66.2% | 68.6% | 67.3% |

## Per-column score (GT-present) by model

| Column | Metric | gpt-4o | gemini-3.1-pro-preview | claude-sonnet-4-6 |
|---|---|--:|--:|--:|
| dtx_name | name_norm_fuzzy | 84.0% | 84.0% | 84.0% |
| is_it_a_diga | exact_norm | 91.4% | 91.4% | 91.4% |
| company_provider | name_norm_fuzzy | 83.3% | 83.3% | 83.3% |
| company_founding_year | int_exact | 8.6% | 8.6% | 8.6% |
| diga_listing_status | exact_norm | 100.0% | 100.0% | 100.0% |
| diga_listing_date | iso_date | 0.0% | 0.0% | 0.0% |
| category | concept_set | 71.6% | 50.5% | 69.7% |
| risk_class | risk_class_norm | 100.0% | 100.0% | 100.0% |
| clinical_area_icd10 | icd10_set | 87.0% | 87.0% | 87.0% |
| rating_on_playstore | float_tolerance | 81.2% | 81.2% | 81.2% |
| reviews_on_playstore | int_exact | 6.7% | 6.7% | 6.7% |
| rating_on_appstore | float_tolerance | 80.0% | 80.0% | 80.0% |
| reviews_on_appstore | int_exact | 7.1% | 7.1% | 7.1% |
| evidence_type | exact_norm | 100.0% | 100.0% | 100.0% |
| primary_end_point_duration_weeks | int_exact | 85.3% | 76.5% | 76.5% |
| follow_up_after_primary_end_point | short_norm_tokenf1 | 3.2% | 9.8% | 9.3% |
| duration_additional_info | bertscore_tokenf1 | 70.3% | 85.4% | 85.0% |
| study_size_participants | int_exact | 80.0% | 85.7% | 82.9% |
| intervention_and_control_group | bertscore_tokenf1 | 74.5% | 82.4% | 81.5% |
| trial_arms | int_exact | 90.9% | 93.9% | 93.9% |
| additional_info_about_trial_arms | bertscore_tokenf1 | 85.4% | 87.1% | 86.9% |
| collected_data | bertscore_tokenf1 | 86.8% | 87.5% | 86.7% |
| key_outcomes_findings | bertscore_tokenf1 | 59.7% | 59.7% | 59.5% |
| trial_start_date | iso_date | 75.0% | 78.1% | 62.5% |
| trial_end_date | iso_date | 78.6% | 75.0% | 67.9% |
| publication_date | iso_date | 31.4% | 31.4% | 28.6% |

## Omission and hallucination rates by model

Omission = GT has a value, model left it blank. Hallucination = GT blank, model filled it. Rates are per column.

### gpt-4o

| Column | Omission rate | Hallucination rate |
|---|--:|--:|
| dtx_name | 0.0% | - |
| is_it_a_diga | 0.0% | - |
| company_provider | 0.0% | - |
| company_founding_year | 85.7% | - |
| diga_listing_status | 0.0% | - |
| diga_listing_date | 0.0% | - |
| category | 0.0% | - |
| risk_class | 0.0% | 66.7% |
| clinical_area_icd10 | 0.0% | - |
| rating_on_playstore | 12.5% | 0.0% |
| reviews_on_playstore | 46.7% | 0.0% |
| rating_on_appstore | 20.0% | 0.0% |
| reviews_on_appstore | 14.3% | 0.0% |
| evidence_type | 0.0% | - |
| primary_end_point_duration_weeks | 5.9% | 100.0% |
| follow_up_after_primary_end_point | 19.0% | 28.6% |
| duration_additional_info | 16.7% | 62.1% |
| study_size_participants | 0.0% | - |
| intervention_and_control_group | 13.3% | 80.0% |
| trial_arms | 3.0% | 100.0% |
| additional_info_about_trial_arms | 3.0% | 100.0% |
| collected_data | 0.0% | 100.0% |
| key_outcomes_findings | 33.3% | 36.4% |
| trial_start_date | 9.4% | 66.7% |
| trial_end_date | 10.7% | 57.1% |
| publication_date | 31.4% | - |

### gemini-3.1-pro-preview

| Column | Omission rate | Hallucination rate |
|---|--:|--:|
| dtx_name | 0.0% | - |
| is_it_a_diga | 0.0% | - |
| company_provider | 0.0% | - |
| company_founding_year | 85.7% | - |
| diga_listing_status | 0.0% | - |
| diga_listing_date | 0.0% | - |
| category | 0.0% | - |
| risk_class | 0.0% | 66.7% |
| clinical_area_icd10 | 0.0% | - |
| rating_on_playstore | 12.5% | 0.0% |
| reviews_on_playstore | 46.7% | 0.0% |
| rating_on_appstore | 20.0% | 0.0% |
| reviews_on_appstore | 14.3% | 0.0% |
| evidence_type | 0.0% | - |
| primary_end_point_duration_weeks | 2.9% | 100.0% |
| follow_up_after_primary_end_point | 28.6% | 21.4% |
| duration_additional_info | 0.0% | 100.0% |
| study_size_participants | 0.0% | - |
| intervention_and_control_group | 0.0% | 100.0% |
| trial_arms | 0.0% | 100.0% |
| additional_info_about_trial_arms | 0.0% | 100.0% |
| collected_data | 0.0% | 100.0% |
| key_outcomes_findings | 33.3% | 36.4% |
| trial_start_date | 9.4% | 66.7% |
| trial_end_date | 14.3% | 71.4% |
| publication_date | 28.6% | - |

### claude-sonnet-4-6

| Column | Omission rate | Hallucination rate |
|---|--:|--:|
| dtx_name | 0.0% | - |
| is_it_a_diga | 0.0% | - |
| company_provider | 0.0% | - |
| company_founding_year | 85.7% | - |
| diga_listing_status | 0.0% | - |
| diga_listing_date | 0.0% | - |
| category | 0.0% | - |
| risk_class | 0.0% | 66.7% |
| clinical_area_icd10 | 0.0% | - |
| rating_on_playstore | 12.5% | 0.0% |
| reviews_on_playstore | 46.7% | 0.0% |
| rating_on_appstore | 20.0% | 0.0% |
| reviews_on_appstore | 14.3% | 0.0% |
| evidence_type | 0.0% | - |
| primary_end_point_duration_weeks | 2.9% | 100.0% |
| follow_up_after_primary_end_point | 14.3% | 42.9% |
| duration_additional_info | 0.0% | 100.0% |
| study_size_participants | 0.0% | - |
| intervention_and_control_group | 0.0% | 100.0% |
| trial_arms | 0.0% | 100.0% |
| additional_info_about_trial_arms | 0.0% | 100.0% |
| collected_data | 0.0% | 100.0% |
| key_outcomes_findings | 33.3% | 36.4% |
| trial_start_date | 9.4% | 66.7% |
| trial_end_date | 14.3% | 71.4% |
| publication_date | 28.6% | - |

## Categorical agreement (Cohen's kappa)

| Column | gpt-4o | gemini-3.1-pro-preview | claude-sonnet-4-6 |
|---|--:|--:|--:|
| is_it_a_diga | 0.00 | 0.00 | 0.00 |
| diga_listing_status | 1.00 | 1.00 | 1.00 |
| risk_class | 0.86 | 0.86 | 0.86 |
| evidence_type | 1.00 | 1.00 | 1.00 |

## Notes

- Free-text columns use BERTScore-F1 (primary) with token-level F1 as cross-check; a sample is exported to [`outcomes_spotcheck.csv`](outcomes_spotcheck.csv) for the manual negation/number review BERTScore can miss.
- `category` uses canonical-concept set overlap (vocabularies differ between GT and models); see methodology limitations.
- Per-study per-field detail is in `<model>_field_scores.json`.
