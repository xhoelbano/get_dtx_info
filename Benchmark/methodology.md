# Benchmark methodology

This document defines the scientific method shared by the benchmarks. Benchmark 1
applies it to study-detection counts.

## 1. Problem statement

We have two sets of clinical studies per Digital Therapeutic (DTx):

- **Ground truth (GT)** - studies curated *manually* in
  [`../Test_Datasets/test_dataset_benchmarking_numbers.json`](../Test_Datasets/test_dataset_benchmarking_numbers.json).
- **Pipeline** - studies discovered *automatically* by the Phase 2 extraction +
  classification pipeline, surfaced through the Phase 3 combined outputs.

We want to quantify how completely and how precisely the pipeline reproduces the
GT. This is a **record-linkage** problem (decide when a GT study and a pipeline
study are the same real study) followed by **information-retrieval evaluation**
(precision / recall / F1).

## 2. The central difficulty: one study, many records

A single clinical study is frequently represented by several artifacts:

- a **protocol / registration** entry in a trial registry
  (ClinicalTrials.gov `NCT…`, German DRKS `DRKS…`, ISRCTN `ISRCTN…`), and
- one or more **publications** of the results (PubMed `PMID`, PubMed Central
  `PMC`, a journal `DOI`), sometimes years later.

In the GT this is encoded in a single row whose `sources_publications` cell
holds **multiple links** and whose `trial_registration_id` holds the canonical
ID. In the pipeline the same study often appears as **multiple rows** (e.g. one
DRKS row for the protocol and one PubMed row for the result). Counting raw rows
would therefore double-count. We solve this by collapsing all records that refer
to the same study into a single **study cluster** before counting.

## 3. Identifier extraction and normalization (deterministic)

For every record (GT row and pipeline row) we build an **identifier set** by
applying fixed regular expressions to both the `trial_registration_id` field and
every URL in `sources_publications`:

| Identifier | Pattern (case-insensitive) | Normalized form |
|---|---|---|
| ClinicalTrials.gov | `NCT\d{8}` | `NCT########` |
| DRKS | `DRKS\d{8}` | `DRKS########` |
| ISRCTN | `ISRCTN\d{8}` | `ISRCTN########` |
| EudraCT / EUCTR | `\d{4}-\d{6}-\d{2}` | `EUCTR:#######...` |
| PubMed | `pubmed.ncbi.nlm.nih.gov/(\d+)`, or a bare all-digit `trial_registration_id` | `PMID:<digits>` |
| PubMed Central | `PMC\d+` | `PMC<digits>` |
| DOI | `10.\d{4,9}/<suffix>` | `DOI:<lowercased>` |

Notes:
- A bare numeric `trial_registration_id` (e.g. `29549193`) is interpreted as a
  **PMID**, matching how the pipeline stores PubMed identifiers.
- Non-identifying URLs (e.g. `diga.bfarm.de` directory pages, company sites,
  journal landing pages without an embedded ID) carry no key; they are retained
  for traceability but never used for matching.
- Namespaces are kept distinct (a `PMID` never matches a `PMC`).

## 4. Study clustering (connected components)

Within each set we treat identifiers as a graph: records are nodes, and two
records are connected if their identifier sets share at least one normalized ID.
The **connected components** (computed with a union-find structure, Hopcroft &
Tarjan 1973) are the study clusters. Each cluster's identifier set is the union
of its members' identifiers.

- GT: each curated row is already one study; clustering additionally detects (and
  reports) any accidental ID sharing between GT rows.
- Pipeline: clustering merges the protocol row(s) and publication row(s) of the
  same study into one, implementing the "count once" rule.

## 5. Matching GT to pipeline

Because registry IDs, PMIDs and PMCIDs are **globally unique**, a GT study and a
pipeline study are declared the **same study** iff their identifier sets
intersect:

```
match(g, p)  <=>  ids(g) ∩ ids(p) ≠ ∅
```

This exact-key linkage (Fellegi & Sunter 1969; Christen 2012) is symmetric and
deterministic. Matching is done on the global identifier space; per-DTx
attribution uses the crosswalk in
[`benchmark_1_study_counts/dtx_crosswalk.json`](benchmark_1_study_counts/dtx_crosswalk.json)
purely for reporting and coverage, never to *create* a match.

## 6. Outcome categories

For a given model run, each GT study and each pipeline cluster falls into:

- **TP (true positive / matched)** - a GT study with >=1 matching pipeline
  cluster.
- **FN (false negative / missed)** - a GT study with no matching pipeline
  cluster.
- **Extra (unadjudicated)** - a pipeline cluster matching no GT study.

### Framing of "Extra" rather than hard false positives

A pipeline study that has no GT counterpart is **not automatically wrong**. The
GT was curated manually and is not guaranteed to be exhaustive, so an extra can
be either (a) genuine pipeline over-extraction / a wrong hit, or (b) a real
study the manual curation missed. We therefore label these **Extras requiring
adjudication** and emit the full list (identifier, DTx, source URL, evidence
type) so they can be reviewed manually. Precision is still reported, but under
the explicit interpretation that it is a **corroboration rate** (share of
pipeline studies confirmed by the GT), i.e. a *lower bound* on true precision
until adjudication.

## 7. KPIs and formulas

Let TP, FN be counts of GT studies and EX the count of extra pipeline clusters.

- **Recall (sensitivity / coverage)**
  `Recall = TP / (TP + FN)`
- **Precision (corroboration rate, pre-adjudication)**
  `Precision = TP / (TP + EX)`
- **F1 score** (harmonic mean; van Rijsbergen 1979)
  `F1 = 2 · Precision · Recall / (Precision + Recall)`
- **Per-DTx Jaccard overlap** of study sets (Jaccard 1912)
  `J = |GT ∩ Pipe| / |GT ∪ Pipe|`

Each metric is reported in two scopes:

1. **Full GT** - over all 26 GT DTx. DTx for which the pipeline verified zero
   studies (it processed them but no candidate passed verification, so they have
   no Phase 3 rows) contribute only FN. This is the honest end-to-end recall of
   the whole pipeline.
2. **Covered subset** - only the GT DTx for which the pipeline returned at least
   one verified study. This isolates extraction/matching quality from the DTx
   where no evidence was found at all.

Secondary: an **evidence-type** view (RCT vs. RWE) comparing the GT label to the
pipeline label for matched studies.

## 8. Why deterministic matching and not an LLM

- **Unambiguous keys.** Trial-registry IDs, PMIDs and PMCIDs are unique public
  identifiers; equality is exact, so a learned/fuzzy matcher adds risk without
  benefit.
- **Reproducibility.** A regex + set-intersection pipeline is fully
  deterministic and re-runnable - essential for a thesis benchmark. LLM matching
  is stochastic and version-dependent.
- **No hallucination surface.** Using an LLM to decide "same study?" could
  invent matches; exact-ID linkage cannot.
- **Where fuzzy/LLM matching could help (and why we don't rely on it):** only
  for sources with *no* extractable identifier (a few company/landing-page
  URLs). These carry no key, are a small minority, and are excluded from the
  matched denominator rather than guessed. Title/DOI fuzzy matching is noted as
  a possible future extension for adjudicating Extras, not for the primary
  metric.

See [`references.md`](references.md) for full citations.
