# Benchmark 2 - Field-level extraction quality (results)

_Generated: 2026-07-04T08:13:09+00:00_

Per-field accuracy of each model's Phase 3 analysis vs. the manual ground truth, scored with data-type-appropriate metrics (see [`../methodology.md`](../methodology.md)).

## Dataset

- GT studies (after clustering): **56**.
- Evaluable (filled GT and found by pipeline): **35**.
- Dropped: **21** - 0 `unfilled_gt`, 21 `not_found_by_pipeline` (see [`dropped_studies.md`](dropped_studies.md)).
- BERTScore active: **True** (roberta-large).

## Headline (per model)

Macro-average over columns of the per-column score on cells where the GT has a value (omissions count as 0; both-empty cells and GT-blank/model-filled additions are excluded, not scored). Micro-average weights by number of GT-present cells.

| Model | Macro (GT-present) | Micro (GT-present) |
|---|--:|--:|
| gpt-4o | 70.5% | 73.8% |
| gemini-3.1-pro-preview | 71.2% | 73.8% |
| claude-sonnet-4-6 | 70.0% | 72.9% |

## Per-column score (GT-present) by model

| Column | Metric | gpt-4o | gemini-3.1-pro-preview | claude-sonnet-4-6 |
|---|---|--:|--:|--:|
| dtx_name | name_norm_fuzzy | 84.0% | 84.0% | 84.0% |
| is_it_a_diga | exact_norm | 91.4% | 91.4% | 91.4% |
| company_provider | name_norm_fuzzy | 83.3% | 83.3% | 83.3% |
| company_founding_year | int_exact | 8.6% | 8.6% | 8.6% |
| diga_listing_status | exact_norm | 100.0% | 100.0% | 100.0% |
| diga_listing_date | iso_date | 80.0% | 80.0% | 80.0% |
| category | concept_set | 71.6% | 50.5% | 69.7% |
| risk_class | risk_class_norm | 100.0% | 100.0% | 100.0% |
| clinical_area_icd10 | icd10_set | 87.0% | 87.0% | 87.0% |
| rating_on_playstore | float_tolerance | 81.2% | 81.2% | 81.2% |
| reviews_on_playstore | int_exact | 6.7% | 6.7% | 6.7% |
| rating_on_appstore | float_tolerance | 80.0% | 80.0% | 80.0% |
| reviews_on_appstore | int_exact | 7.1% | 7.1% | 7.1% |
| evidence_type | exact_norm | 100.0% | 100.0% | 100.0% |
| primary_end_point_duration_weeks | int_exact | 85.3% | 79.4% | 79.4% |
| follow_up_after_primary_end_point | duration_weeks_exact | 38.1% | 47.6% | 28.6% |
| duration_additional_info | bertscore_tokenf1 | 70.3% | 85.4% | 85.0% |
| study_size_participants | int_exact | 77.1% | 82.9% | 80.0% |
| intervention_and_control_group | bertscore_tokenf1 | 74.5% | 82.4% | 81.5% |
| trial_arms | int_exact | 90.9% | 93.9% | 93.9% |
| additional_info_about_trial_arms | bertscore_tokenf1 | 85.4% | 87.1% | 86.9% |
| collected_data | bertscore_tokenf1 | 86.8% | 87.5% | 86.7% |
| key_outcomes_findings | bertscore_tokenf1 | 59.7% | 59.7% | 59.5% |
| trial_start_date | iso_date | 75.0% | 78.1% | 62.5% |
| trial_end_date | iso_date | 78.6% | 75.0% | 67.9% |
| publication_date | iso_date | 31.4% | 31.4% | 28.6% |

## Omissions and unverified additions by model

**Omission** = GT has a value, model left it blank (a genuine miss; scored as 0 in the GT-present score). **Unverified addition** = GT blank, model filled it. Because the analysis GT is a deliberately small, incomplete manual subset, an addition is a *candidate* value that needs a source check, not an automatic error - so it is reported as a count and is never scored (mirrors Benchmark 1 'Extras'). The addition rate is shown only where at least 10 GT-blank cells exist (`-` otherwise), because a percentage over one or two cells is noise.

### gpt-4o

| Column | Omissions (of GT-present) | Omission rate | Unverified additions (of GT-blank) | Addition rate |
|---|--:|--:|--:|--:|
| dtx_name | 0/35 | 0.0% | 0/0 | - |
| is_it_a_diga | 0/35 | 0.0% | 0/0 | - |
| company_provider | 0/35 | 0.0% | 0/0 | - |
| company_founding_year | 30/35 | 85.7% | 0/0 | - |
| diga_listing_status | 0/35 | 0.0% | 0/0 | - |
| diga_listing_date | 0/35 | 0.0% | 0/0 | - |
| category | 0/35 | 0.0% | 0/0 | - |
| risk_class | 0/32 | 0.0% | 2/3 | - |
| clinical_area_icd10 | 0/35 | 0.0% | 0/0 | - |
| rating_on_playstore | 2/16 | 12.5% | 0/19 | 0.0% |
| reviews_on_playstore | 7/15 | 46.7% | 0/20 | 0.0% |
| rating_on_appstore | 3/15 | 20.0% | 0/20 | 0.0% |
| reviews_on_appstore | 2/14 | 14.3% | 0/21 | 0.0% |
| evidence_type | 0/35 | 0.0% | 0/0 | - |
| primary_end_point_duration_weeks | 2/34 | 5.9% | 1/1 | - |
| follow_up_after_primary_end_point | 4/21 | 19.0% | 4/14 | 28.6% |
| duration_additional_info | 1/6 | 16.7% | 18/29 | 62.1% |
| study_size_participants | 0/35 | 0.0% | 0/0 | - |
| intervention_and_control_group | 4/30 | 13.3% | 4/5 | - |
| trial_arms | 1/33 | 3.0% | 2/2 | - |
| additional_info_about_trial_arms | 1/33 | 3.0% | 2/2 | - |
| collected_data | 0/31 | 0.0% | 4/4 | - |
| key_outcomes_findings | 8/24 | 33.3% | 4/11 | 36.4% |
| trial_start_date | 3/32 | 9.4% | 2/3 | - |
| trial_end_date | 3/28 | 10.7% | 4/7 | - |
| publication_date | 11/35 | 31.4% | 0/0 | - |

### gemini-3.1-pro-preview

| Column | Omissions (of GT-present) | Omission rate | Unverified additions (of GT-blank) | Addition rate |
|---|--:|--:|--:|--:|
| dtx_name | 0/35 | 0.0% | 0/0 | - |
| is_it_a_diga | 0/35 | 0.0% | 0/0 | - |
| company_provider | 0/35 | 0.0% | 0/0 | - |
| company_founding_year | 30/35 | 85.7% | 0/0 | - |
| diga_listing_status | 0/35 | 0.0% | 0/0 | - |
| diga_listing_date | 0/35 | 0.0% | 0/0 | - |
| category | 0/35 | 0.0% | 0/0 | - |
| risk_class | 0/32 | 0.0% | 2/3 | - |
| clinical_area_icd10 | 0/35 | 0.0% | 0/0 | - |
| rating_on_playstore | 2/16 | 12.5% | 0/19 | 0.0% |
| reviews_on_playstore | 7/15 | 46.7% | 0/20 | 0.0% |
| rating_on_appstore | 3/15 | 20.0% | 0/20 | 0.0% |
| reviews_on_appstore | 2/14 | 14.3% | 0/21 | 0.0% |
| evidence_type | 0/35 | 0.0% | 0/0 | - |
| primary_end_point_duration_weeks | 1/34 | 2.9% | 1/1 | - |
| follow_up_after_primary_end_point | 6/21 | 28.6% | 3/14 | 21.4% |
| duration_additional_info | 0/6 | 0.0% | 29/29 | 100.0% |
| study_size_participants | 0/35 | 0.0% | 0/0 | - |
| intervention_and_control_group | 0/30 | 0.0% | 5/5 | - |
| trial_arms | 0/33 | 0.0% | 2/2 | - |
| additional_info_about_trial_arms | 0/33 | 0.0% | 2/2 | - |
| collected_data | 0/31 | 0.0% | 4/4 | - |
| key_outcomes_findings | 8/24 | 33.3% | 4/11 | 36.4% |
| trial_start_date | 3/32 | 9.4% | 2/3 | - |
| trial_end_date | 4/28 | 14.3% | 5/7 | - |
| publication_date | 10/35 | 28.6% | 0/0 | - |

### claude-sonnet-4-6

| Column | Omissions (of GT-present) | Omission rate | Unverified additions (of GT-blank) | Addition rate |
|---|--:|--:|--:|--:|
| dtx_name | 0/35 | 0.0% | 0/0 | - |
| is_it_a_diga | 0/35 | 0.0% | 0/0 | - |
| company_provider | 0/35 | 0.0% | 0/0 | - |
| company_founding_year | 30/35 | 85.7% | 0/0 | - |
| diga_listing_status | 0/35 | 0.0% | 0/0 | - |
| diga_listing_date | 0/35 | 0.0% | 0/0 | - |
| category | 0/35 | 0.0% | 0/0 | - |
| risk_class | 0/32 | 0.0% | 2/3 | - |
| clinical_area_icd10 | 0/35 | 0.0% | 0/0 | - |
| rating_on_playstore | 2/16 | 12.5% | 0/19 | 0.0% |
| reviews_on_playstore | 7/15 | 46.7% | 0/20 | 0.0% |
| rating_on_appstore | 3/15 | 20.0% | 0/20 | 0.0% |
| reviews_on_appstore | 2/14 | 14.3% | 0/21 | 0.0% |
| evidence_type | 0/35 | 0.0% | 0/0 | - |
| primary_end_point_duration_weeks | 1/34 | 2.9% | 1/1 | - |
| follow_up_after_primary_end_point | 3/21 | 14.3% | 6/14 | 42.9% |
| duration_additional_info | 0/6 | 0.0% | 29/29 | 100.0% |
| study_size_participants | 0/35 | 0.0% | 0/0 | - |
| intervention_and_control_group | 0/30 | 0.0% | 5/5 | - |
| trial_arms | 0/33 | 0.0% | 2/2 | - |
| additional_info_about_trial_arms | 0/33 | 0.0% | 2/2 | - |
| collected_data | 0/31 | 0.0% | 4/4 | - |
| key_outcomes_findings | 8/24 | 33.3% | 4/11 | 36.4% |
| trial_start_date | 3/32 | 9.4% | 2/3 | - |
| trial_end_date | 4/28 | 14.3% | 5/7 | - |
| publication_date | 10/35 | 28.6% | 0/0 | - |

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
