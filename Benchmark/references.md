# References

Scientific literature underpinning the benchmark methodology.

## Record linkage / entity resolution

- **Fellegi, I. P., & Sunter, A. B. (1969).** A Theory for Record Linkage.
  *Journal of the American Statistical Association*, 64(328), 1183-1210.
  doi:10.1080/01621459.1969.10501049.
  *Foundational theory of deciding whether two records refer to the same
  entity; motivates using strong, unique identifiers as matching keys.*
- **Christen, P. (2012).** *Data Matching: Concepts and Techniques for Record
  Linkage, Entity Resolution, and Duplicate Detection.* Springer.
  doi:10.1007/978-3-642-31164-2.
  *Comprehensive treatment of deterministic vs. probabilistic matching and
  blocking; supports exact-identifier (deterministic) matching when reliable
  keys exist.*

## Clustering / connected components

- **Hopcroft, J., & Tarjan, R. (1973).** Algorithm 447: Efficient algorithms
  for graph manipulation. *Communications of the ACM*, 16(6), 372-378.
  *Connected-components / union-find, used to merge study records that share an
  identifier into a single study cluster.*

## Evaluation metrics

- **van Rijsbergen, C. J. (1979).** *Information Retrieval* (2nd ed.).
  Butterworths.
  *Definitions of precision, recall, and the F-measure.*
- **Powers, D. M. W. (2011).** Evaluation: From Precision, Recall and F-Measure
  to ROC, Informedness, Markedness & Correlation. *Journal of Machine Learning
  Technologies*, 2(1), 37-63.
  *Critical discussion of precision/recall/F1 and their interpretation.*
- **Jaccard, P. (1912).** The distribution of the flora in the alpine zone.
  *New Phytologist*, 11(2), 37-50.
  *The Jaccard index used here to quantify set overlap between the curated and
  the automatically extracted study sets per DTx.*

## Field-level extraction evaluation (Benchmark 2)

### Text-similarity and span metrics

- **Zhang, T., Kishore, V., Wu, F., Weinberger, K. Q., & Artzi, Y. (2020).**
  BERTScore: Evaluating Text Generation with BERT. *International Conference on
  Learning Representations (ICLR 2020).* arXiv:1904.09675.
  *Embedding-based precision/recall/F1 for text; primary metric for the free-text
  columns because it credits paraphrase while remaining deterministic.*
- **Rajpurkar, P., Zhang, J., Lopyrev, K., & Liang, P. (2016).** SQuAD: 100,000+
  Questions for Machine Comprehension of Text. *EMNLP 2016.*
  *Token-level F1 and exact match; used as the lexical cross-check for free text.*
- **Lin, C.-Y. (2004).** ROUGE: A Package for Automatic Evaluation of Summaries.
  *ACL Text Summarization Branches Out Workshop.*
  *Cited as a rejected metric for the free-text columns (n-gram overlap punishes
  correct paraphrase).*
- **Papineni, K., Roukos, S., Ward, T., & Zhu, W.-J. (2002).** BLEU: a Method for
  Automatic Evaluation of Machine Translation. *ACL 2002.*
  *Cited as a rejected metric for the free-text columns.*

### Agreement and categorical metrics

- **Cohen, J. (1960).** A Coefficient of Agreement for Nominal Scales.
  *Educational and Psychological Measurement*, 20(1), 37-46.
  *Cohen's kappa for chance-corrected agreement on categorical columns.*
- **Artstein, R., & Poesio, M. (2008).** Inter-Coder Agreement for Computational
  Linguistics. *Computational Linguistics*, 34(4), 555-596.
  *Correct application/interpretation of agreement coefficients, incl. the kappa
  paradox on skewed labels.*

### Set / entity matching

- **Batista, D. (2018).** Named-Entity Evaluation Metrics Based on Entity-Level.
  *davidsbatista.net* (formalizes MUC / SemEval-2013 strict-exact-partial-type
  matching).
  *Strict-vs-relaxed vocabulary for the ICD-10 set column.*
- (Jaccard 1912 - listed above - reused for set overlap.)

### Structured-extraction evaluation frameworks (schema-driven, per-field metrics)

- **ExtractBench: A Benchmark and Evaluation Methodology for Complex Structured
  Extraction.** arXiv:2602.12247.
  *Per-field `evaluation_config` (exact / fuzzy / numeric-tolerance / semantic /
  array-alignment) declared in the schema; motivates the column_types.json
  approach.*
- **RealDocBench: A Benchmark for Field-Level QA and Layout Understanding on
  Real-World Regulated Documents.** arXiv:2606.07401.
  *Type-tolerant equality + conservative fuzzy matching for long string fields;
  per-field vs strict per-record accuracy; omission/hallucination diagnostics.*
- **ContextualAI/extract-bench** (GitHub).
  *Open metric presets: string_exact, string_fuzzy (Levenshtein), string_url,
  number_exact, number_tolerance, integer_exact, boolean_exact.*
- **FAIRmat-NFDI/extract-eval** (GitHub).
  *Per-field precision/recall/F1 for LLM JSON extraction with explicit
  omission/hallucination/mismatch tracing.*

> Note on the LLM-as-judge divergence: the structured-extraction frameworks above
> use an LLM judge for free-text fields. Benchmark 2 deliberately substitutes
> BERTScore for those fields to avoid self-enhancement / circularity bias, since
> the systems under evaluation are themselves GPT-4o / Claude / Gemini. See
> [`benchmark_2_field_extraction/methodology.md`](benchmark_2_field_extraction/methodology.md).

## Domain identifiers (for reference)

- ClinicalTrials.gov NCT numbers; German Clinical Trials Register (DRKS) IDs;
  ISRCTN registry IDs; PubMed PMIDs and PubMed Central PMCIDs; Digital Object
  Identifiers (DOI, ISO 26324). These public, globally unique identifiers are
  the matching keys used in Benchmark 1.
