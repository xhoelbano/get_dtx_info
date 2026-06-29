# Benchmark 1 - Study detection (counts)

_Generated: 2026-06-29T15:13:12+00:00_

Automated pipeline study detection vs. the manually curated ground truth, using deterministic identifier-based record linkage (see [`../../methodology.md`](../../methodology.md)).

## Dataset overview

- Ground-truth studies: **96** across **26** DTx.
- DTx for which the pipeline returned at least one verified study (evaluable / 'covered'): **19** (79 GT studies).
- DTx for which the pipeline found **zero** verified studies (the evidence search ran but no candidate passed verification, so there was nothing to evaluate): **7** (17 GT studies) - neolexon Aphasia, Selfapy's online course for chronic pain, Selfapy's online course for panic disorder, Beats Medical Parkinson’s, Feel DTx, Embr Wave 2, Sword Thrive.

All three models analyze the **same** Phase 2 evidence set (extracted with gpt-4o, Website Search/Browser Use ON), so the study set is identical across models; running all three is a determinism check.

## Headline results

**Full GT scope** (all DTx; DTx with zero verified pipeline studies contribute only misses):

| Model | Pipeline studies | TP (matched) | FN (missed) | Extras | Recall | Precision* | F1 |
|---|--:|--:|--:|--:|--:|--:|--:|
| gpt-4o | 109 | 61 | 35 | 32 | 63.5% | 65.6% | 64.6% |
| gemini-3.1-pro-preview | 109 | 61 | 35 | 32 | 63.5% | 65.6% | 64.6% |
| claude-sonnet-4-6 | 109 | 61 | 35 | 32 | 63.5% | 65.6% | 64.6% |

**Covered-subset scope** (only the DTx for which the pipeline returned at least one verified study):

| Model | TP (matched) | FN (missed) | Extras | Recall | Precision* | F1 |
|---|--:|--:|--:|--:|--:|--:|
| gpt-4o | 61 | 18 | 32 | 77.2% | 65.6% | 70.9% |
| gemini-3.1-pro-preview | 61 | 18 | 32 | 77.2% | 65.6% | 70.9% |
| claude-sonnet-4-6 | 61 | 18 | 32 | 77.2% | 65.6% | 70.9% |

\* Precision is a **corroboration rate** (share of pipeline studies confirmed by the GT) and a lower bound until the Extras are adjudicated; see the Extras section.

## Per-DTx breakdown

Identifier-based matching is identical across the three models (same evidence set); the table below is representative (`gpt-4o`). `Jaccard` = |GT ∩ Pipeline| / |GT ∪ Pipeline| on study sets.

| DTx | Pipeline evidence | GT | Pipeline | Matched | Missed | Jaccard |
|---|:--:|--:|--:|--:|--:|--:|
| Attexis- digital therapy for ADHD in adulthood | yes | 2 | 3 | 2 | 0 | 66.7% |
| Cara Care for IBS | yes | 2 | 1 | 1 | 1 | 50.0% |
| Companion Patella powered by medi | yes | 2 | 1 | 1 | 1 | 50.0% |
| elevida | yes | 1 | 2 | 1 | 0 | 50.0% |
| Hellobetter Chronic Pain | yes | 3 | 3 | 2 | 1 | 50.0% |
| HelloBetter Diabetes | yes | 2 | 1 | 1 | 1 | 50.0% |
| Kaia back pain - back exercises for home | yes | 10 | 3 | 3 | 7 | 30.0% |
| deprexis DiGA | yes | 20 | 46 | 19 | 1 | 40.4% |
| neolexon Aphasia | none found | 1 | 0 | 0 | 1 | 0.0% |
| somnio | yes | 10 | 8 | 7 | 3 | 63.6% |
| Optimune | yes | 2 | 5 | 2 | 0 | 40.0% |
| Orthopy for knee injuries | yes | 6 | 6 | 6 | 0 | 100.0% |
| Re.flex | yes | 3 | 3 | 2 | 1 | 50.0% |
| Rehappy | yes | 1 | 1 | 1 | 0 | 100.0% |
| Selfapy's online course for chronic pain | none found | 1 | 0 | 0 | 1 | 0.0% |
| Selfapy's online course for panic disorder | none found | 1 | 0 | 0 | 1 | 0.0% |
| Companion® Shoulder | yes | 1 | 1 | 1 | 0 | 100.0% |
| Vivira | yes | 2 | 12 | 2 | 0 | 16.7% |
| Beats Medical Parkinson’s | none found | 1 | 0 | 0 | 1 | 0.0% |
| RelieveVRx | yes | 5 | 4 | 3 | 2 | 50.0% |
| Caterna Vision Therapy | yes | 2 | 2 | 2 | 0 | 100.0% |
| Somryst | yes | 4 | 6 | 4 | 0 | 66.7% |
| Feel DTx | none found | 1 | 0 | 0 | 1 | 0.0% |
| Embr Wave 2 | none found | 2 | 0 | 0 | 2 | 0.0% |
| Sword Bloom | yes | 1 | 1 | 1 | 0 | 100.0% |
| Sword Thrive | none found | 10 | 0 | 0 | 10 | 0.0% |

## Missed ground-truth studies (false negatives)

Studies present in the GT but not recovered by the pipeline. Where 'Pipeline evidence' is 'none found', the pipeline processed the DTx but verified zero studies, so every GT study for it is necessarily a miss (there was nothing to evaluate); where it is 'yes', the miss is a genuine extraction/matching failure within a DTx that did yield verified evidence.

| DTx | Pipeline evidence | GT identifiers | Evidence type |
|---|:--:|---|:--:|
| Cara Care for IBS | yes | (no identifier) | RWE |
| Companion Patella powered by medi | yes | DOI:10.1007/s00402-025-05787-y | RCT |
| Hellobetter Chronic Pain | yes | DRKS00014619, PMID:32883721, PMID:36360738 | RCT |
| HelloBetter Diabetes | yes | PMID:24862240 | RCT |
| Kaia back pain - back exercises for home | yes | DRKS00015048, PMID:32765057, PMID:38952994 | RCT |
| Kaia back pain - back exercises for home | yes | PMID:29203460 | RWE |
| Kaia back pain - back exercises for home | yes | PMID:29875088 | RWE |
| Kaia back pain - back exercises for home | yes | PMID:32547175 | RWE |
| Kaia back pain - back exercises for home | yes | PMID:34751664 | RWE |
| Kaia back pain - back exercises for home | yes | NCT04290078 | RCT |
| Kaia back pain - back exercises for home | yes | NCT04411108 | RCT |
| deprexis DiGA | yes | (no identifier) | RWE |
| neolexon Aphasia | none found | DRKS00026233 | RCT |
| somnio | yes | DOI:10.1007/s11818-023-00422-7?utm_source=rct_congratemailt&utm_medium=email&utm_campaign=oa_20231017&utm_content=10.1007%2fs11818-023-00422-7 | RWE |
| somnio | yes | NCT02629913, PMID:30135758 | RCT |
| somnio | yes | DRKS00033527, PMID:42146896 | RCT |
| Re.flex | yes | PMID:40523788 | RCT |
| Selfapy's online course for chronic pain | none found | DRKS00031521 | RCT |
| Selfapy's online course for panic disorder | none found | DRKS00023800, PMID:40173444 | RCT |
| Beats Medical Parkinson’s | none found | (no identifier) | - |
| RelieveVRx | yes | NCT04415177, PMID:33464215, PMID:35612905 | RCT |
| RelieveVRx | yes | NCT06248216 | RCT |
| Feel DTx | none found | PMID:39972054, DOI:10.1038/s41746-025-01511-7 | RCT |
| Embr Wave 2 | none found | NCT04892914, DOI:10.1200/jco.2022.40.16_suppl.5067 | RWE |
| Embr Wave 2 | none found | PMID:31801384 | RCT |
| Sword Thrive | none found | NCT04808141, PMID:37420107, DOI:10.1038/s41746-023-00870-3 | RCT |
| Sword Thrive | none found | NCT03047252, PMID:30816849 | RCT |
| Sword Thrive | none found | NCT03045549, PMID:31228176, DOI:10.2196/14523 | RCT |
| Sword Thrive | none found | NCT05417685, PMID:39120221, PMID:39124635, PMID:39802416, PMID:41629208 | RWE |
| Sword Thrive | none found | NCT04092946, PMID:34983488, PMID:35813029, PMID:35954555, PMID:36003064, PMID:36011251, PMID:36200858, PMID:36553873, PMID:36636267, PMID:39124635, PMID:40852400 | RWE |
| Sword Thrive | none found | NCT04819022, PMID:34499038 | RWE |
| Sword Thrive | none found | NCT04401683 | RCT |
| Sword Thrive | none found | NCT03750500 | RCT |
| Sword Thrive | none found | NCT03648060 | RWE |
| Sword Thrive | none found | NCT03648047 | RCT |

## Extras requiring adjudication (pipeline studies with no GT match)

These pipeline studies did not match any GT study. They are **not** automatically wrong: the manual GT may be incomplete. Review each to decide whether it is a genuine extra study (GT gap) or a pipeline over-extraction / wrong hit.

| DTx (pipeline) | GT DTx | Identifiers | Evidence type | Source |
|---|---|---|:--:|---|
| deprexis | deprexis DiGA | NCT02196896 | RCT | https://clinicaltrials.gov/study/NCT02196896 |
| deprexis | deprexis DiGA | PMID:31710772 | RCT | https://pubmed.ncbi.nlm.nih.gov/31710772/ |
| deprexis | deprexis DiGA | PMID:37590052 | RCT | https://pubmed.ncbi.nlm.nih.gov/37590052/ |
| deprexis | deprexis DiGA | PMID:29154168 | RCT | https://pubmed.ncbi.nlm.nih.gov/29154168/ |
| deprexis | deprexis DiGA | PMID:29883479 | RCT | https://pubmed.ncbi.nlm.nih.gov/29883479/ |
| deprexis | deprexis DiGA | PMID:31175475 | RCT | https://pubmed.ncbi.nlm.nih.gov/31175475/ |
| deprexis | deprexis DiGA | PMID:29331706 | RCT | https://pubmed.ncbi.nlm.nih.gov/29331706/ |
| deprexis | deprexis DiGA | PMID:33500282 | RCT | https://pubmed.ncbi.nlm.nih.gov/33500282/ |
| deprexis | deprexis DiGA | PMID:31682137 | RCT | https://pubmed.ncbi.nlm.nih.gov/31682137/ |
| deprexis | deprexis DiGA | PMID:31240727 | RCT | https://pubmed.ncbi.nlm.nih.gov/31240727/ |
| deprexis | deprexis DiGA | PMID:38912859 | RCT | https://pubmed.ncbi.nlm.nih.gov/38912859/ |
| deprexis | deprexis DiGA | PMID:30721109 | RCT | https://pubmed.ncbi.nlm.nih.gov/30721109/ |
| deprexis | deprexis DiGA | PMID:29751239 | RCT | https://pubmed.ncbi.nlm.nih.gov/29751239/ |
| deprexis | deprexis DiGA | PMID:31542565 | RCT | https://pubmed.ncbi.nlm.nih.gov/31542565/ |
| deprexis | deprexis DiGA | PMID:29489038 | RCT | https://pubmed.ncbi.nlm.nih.gov/29489038/ |
| deprexis | deprexis DiGA | PMID:32663998 | RCT | https://pubmed.ncbi.nlm.nih.gov/32663998/ |
| deprexis | deprexis DiGA | PMID:28797829 | RCT | https://pubmed.ncbi.nlm.nih.gov/28797829/ |
| deprexis | deprexis DiGA | PMID:28710212 | RCT | https://pubmed.ncbi.nlm.nih.gov/28710212/ |
| deprexis | deprexis DiGA | DOI:10.1177/13591053251356885 | RCT | ['https://doi.org/10.1177/13591053251356885'] |
| HelloBetter Chronische Schmerzen | Hellobetter Chronic Pain | DRKS00037509 | RCT | https://drks.de/search/en/trial/DRKS00037509/details |
| optimune | Optimune | NCT03448250 | RCT | https://clinicaltrials.gov/study/NCT03448250 |
| optimune | Optimune | DOI:10.1371/journal.pone.0251276 | RCT | ['https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0251276'] |
| ViViRA, Vivira | Vivira | DRKS00031254 | RCT | https://drks.de/search/en/trial/DRKS00031254/details |
| ViViRA, Vivira | Vivira | PMID:39627851 | RCT | https://pubmed.ncbi.nlm.nih.gov/39627851/ |
| ViViRA, Vivira | Vivira | PMID:42342871 | RCT | https://pubmed.ncbi.nlm.nih.gov/42342871/ |
| ViViRA, Vivira | Vivira | DRKS00021785 | RWE | https://drks.de/search/en/trial/DRKS00021785/details |
| ViViRA, Vivira | Vivira | DRKS00028920 | RWE | https://drks.de/search/en/trial/DRKS00028920/details |
| ViViRA, Vivira | Vivira | PMID:36459399 | RWE | https://pubmed.ncbi.nlm.nih.gov/36459399/ |
| ViViRA, Vivira | Vivira | PMID:37830652 | RWE | https://pubmed.ncbi.nlm.nih.gov/37830652/ |
| Somryst | Somryst | PMID:38018031 | RCT | https://pubmed.ncbi.nlm.nih.gov/38018031/ |
| Somryst | Somryst | PMID:40469887 | RWE | https://pubmed.ncbi.nlm.nih.gov/40469887/ |
| ViViRA | Vivira | DRKS00037490 | RCT | https://drks.de/search/en/trial/DRKS00037490/details |

## Cross-model comparison

All three models produce **identical** detection counts, as expected: Benchmark 1 measures the shared Phase 2 evidence extraction, not the per-model Phase 3 analysis. The numbers above therefore characterize the extraction pipeline itself.

## Interpretation

- **End-to-end recall** (full GT) is 63.5%: the pipeline recovered 61 of 96 curated studies. The gap is dominated by the 7 DTx for which the pipeline found zero verified studies (no evidence to evaluate).
- **Recall on DTx that yielded evidence** rises to 77.2% (61/79), isolating extraction/matching quality from the zero-evidence DTx.
- **Extras**: 32 pipeline studies have no GT match and need adjudication; precision is reported as a lower bound until then.
