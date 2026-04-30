# Master Thesis Mid-Term Status Presentation

Working title: **An Evidence-Generation Framework for Digital Therapeutics**

Presentation length: **10 minutes**
Audience: thesis supervisors
Purpose: status update, methodological feedback, alignment on remaining scope

---

## Slide 1: Title Slide

**Slide title:** Master Thesis Status: An Evidence-Generation Framework for Digital Therapeutics

**Content:**

- Xhoel Bano
- Mid-Term Status Presentation -- April 2026
- Supervisors: [Professor names]

**Speaker notes:**

Good morning/afternoon. This is a status update on my master thesis, not a final defense. I will show where the framework stands, what results it has produced so far, and where I need your feedback before entering the validation and writing phase.

**Timing:** 0:00 -- 0:30

---

## Slide 2: Problem Statement

**Slide title:** DTx Evidence Is Fragmented Across Disconnected Sources

**Visual:** `presentation_diagrams/1_evidence_landscape.xml`

**Bullets:**

- Digital Therapeutics (DTx) need clinical evidence for regulatory approval, reimbursement, and market confidence
- Evidence is scattered: trial registries (DRKS, ClinicalTrials.gov, ISRCTN), publications (PubMed), company websites, financial databases (Pitchbook)
- No single source holds the complete picture -- no cross-references between registries
- Manual collection is slow, incomplete, and not reproducible

**Speaker notes:**

The core problem is not that evidence does not exist. The problem is that evidence for a single product is distributed across sources with completely different structures and access methods. Take "deprexis" as an example: it has 25+ publications on PubMed, 15 registered trials on ClinicalTrials.gov, 2 German-specific registrations on DRKS, and 3 international registrations on ISRCTN. Yet no single source has the complete picture. Furthermore, 16 of the 76 German DiGA products have zero registry evidence at all. Without an automated pipeline, building a cross-product evidence comparison requires hours of manual work per product and is inherently error-prone.

**Timing:** 0:30 -- 1:30

---

## Slide 3: Research Questions and Contribution

**Slide title:** Research Questions

**Bullets:**

- **Main RQ:** How can an LLM-based framework automatically collect, link, and structure RCT and RWE evidence for DTx products?
- **Schema RQ:** Which taxonomy captures heterogeneous evidence sources well enough for cross-product analysis?
- **Evaluation RQ:** How reliable is LLM-based extraction and classification compared to a manual gold standard?
- **Transfer RQ:** Can the German DiGA workflow generalize to the less regulated US market?

**Contribution:**

- Methodological: evidence schema + two-layer classification architecture + validation methodology
- Practical: working pipeline that turns scattered raw evidence into a structured, reproducible evidence catalog

**Speaker notes:**

The thesis contribution has two sides. On the methodological side, I define an evidence schema, a two-layer classification approach, and a validation setup using a manual gold standard. On the practical side, I build a pipeline that turns scattered raw sources into structured study-level rows that can be compared across products. The financial and US components are important for the full thesis scope, but they depend on first making the German evidence extraction reliable and validated.

**Timing:** 1:30 -- 2:30

---

## Slide 4: Pipeline Overview (Roadmap)

**Slide title:** End-to-End Pipeline: From DTx Name to Evidence Profile

**Visual:** `presentation_diagrams/2_pipeline_overview.xml`

**Bullets:**

- Input: any DTx product name triggers the full pipeline
- Stage 0: Metadata Collection (BfArM scraping for DE, LLM research for US)
- Stage 1: Evidence Collection from 5 sources (4 registries + website fallback)
- Stage 2: Two-Layer LLM Classification (relevance verification + RCT/RWE)
- Stage 3: Analysis and Multi-LLM Benchmarking against Gold Standard
- I will now walk through each module with its results, achievements, and challenges

**Speaker notes:**

Before diving into each module, here is the full pipeline at a glance. The design principle is minimal input, maximum output: a researcher types a single DTx product name, and the framework handles everything from metadata collection through evidence search, classification, and analysis. I will now present each stage individually, showing what it does, what it has produced, and the key challenges I encountered building it.

**Timing:** 2:30 -- 3:00

---

## Slide 5: Module -- Pipeline Input

**Slide title:** Pipeline Input and DTx Identification

**Visual:** `presentation_diagrams/7_module_input.xml`

**Bullets:**

- 76 German DiGAs scraped from BfArM, 38 US companies researched via LLM
- Generalized input: a single DTx name triggers the full pipeline end-to-end
- Python CLI (Click) with 6 commands: scrape-dtx, scrape-usa, find-evidence, analyze-evidence, evidence-summary, translate
- **Challenge:** No unified global DTx registry; Germany has BfArM, US has no centralized list
- **Challenge:** Product naming inconsistencies across countries and sources

**Speaker notes:**

The core design goal is simplicity: a researcher types one product name and gets everything. The CLI makes the pipeline fully scriptable and reproducible. The main challenge at this level is that there is no global DTx registry. Germany has BfArM, which I can scrape, but the US has no centralized directory, so I had to use LLM-based research to build the US catalog. Product names also vary across sources, which creates downstream matching problems.

**Timing:** 3:00 -- 3:30

---

## Slide 6: Module -- Stage 0: Metadata Collection

**Slide title:** Stage 0: Building the Product Catalog

**Visual:** `presentation_diagrams/8_module_stage0_metadata.xml`

**Bullets:**

- **Germany path:** Playwright scrapes BfArM with 3 custom JS extraction scripts; 76 products with 20+ fields each in ~4 minutes
- **USA path:** LLM researches each company from a CSV seed list; produces unified schema matching Germany
- **Achievement:** Unified JSON schema across countries despite completely different source structures
- **Achievement:** Robust handling of dynamic JS-rendered pages (BfArM has no static HTML)
- **Challenge:** BfArM layout changes without notice; extraction scripts need maintenance
- **Challenge:** LLM hallucination risk for US data with no authoritative registry to verify against

**Speaker notes:**

Stage 0 builds the foundation. For Germany, BfArM uses heavily dynamic JavaScript rendering, so standard HTTP requests return empty pages. I use Playwright with three custom JS scripts to extract list pages, detail pages, and ICD-10 tables. For the US, there is no centralized registry, so the LLM researches each company and generates structured metadata. The key achievement is that both paths produce the same JSON schema, so the downstream pipeline does not care which country the data came from. The model name is recorded in the output for traceability, which is important since different LLMs may produce slightly different US metadata.

**Timing:** 3:30 -- 4:15

---

## Slide 7: Module -- Stage 1: Evidence Collection

**Slide title:** Stage 1: Searching 5 Sources for Evidence

**Visual:** `presentation_diagrams/9_module_stage1_collection.xml`

**Bullets:**

- 1,049 candidates collected from 5 sources: PubMed (606), DRKS (318), ClinicalTrials.gov (91), ISRCTN (30), Company Websites (4, fallback for 16 zero-evidence products)
- Deterministic query generation: "product name" and "product + company name" sent to all registries in parallel
- **Achievement:** Every candidate preserved as raw XML/JSON/HTML for reproducibility
- **Achievement:** Website fallback with AI browser agent for products with no registry evidence
- **Challenge:** APIs vs. browser scraping duality; Cloudflare bot protection on company websites
- **Challenge:** API rate limits (PubMed 3-10 req/sec, CT.gov similar) require built-in throttling

**Speaker notes:**

Stage 1 is about maximizing recall. I want every potentially relevant study. The five sources have completely different access methods: PubMed gives me structured XML via its E-utilities API, ClinicalTrials.gov returns JSON, but DRKS and ISRCTN have no API at all, requiring full Playwright browser automation. For products with zero registry evidence, a browser-use AI agent navigates company websites to find research pages. The key insight here is that not all collected candidates are relevant. "deprexis" returns 25+ PubMed results, but some only mention it as a comparator. That is why Stage 2 exists.

**Timing:** 4:15 -- 5:15

---

## Slide 8: Module -- Stage 2: Two-Layer Classification

**Slide title:** Stage 2: LLM Verification and Classification

**Visual:** `presentation_diagrams/10_module_stage2_classification.xml`

**Bullets:**

- Layer 1 (recall): all candidates from 5 sources
- Layer 2a (precision): LLM reads title + abstract + raw page content, asks "Is this study about THIS specific DTx?"
- Layer 2b (classification): verified studies classified as RCT or RWE by analyzing study design
- **Results:** 247 verified studies (172 RCT + 75 RWE) from 1,049 candidates; 50 products with RCT, 40 with RWE, 16 with zero registry evidence
- **Achievement:** Keyword-based fallback classifier ensures pipeline does not stall on rate limits
- **Achievement:** Full auditability -- every rejection stored with LLM reason and confidence score
- **Challenge:** Product name ambiguity creates false positives (e.g., "deprexis" as comparator)
- **Challenge:** German compound names like "glucura-diabetestherapie" require special regex handling

**Speaker notes:**

This is the core intellectual contribution of the framework. The two-layer design deliberately separates recall from precision. In Layer 1, we collected 1,049 candidates across all 76 products. In Layer 2, the LLM reads each candidate's full context and makes two decisions: first, is this study actually about this specific product? Second, if it is relevant, is it an RCT or RWE? The result: 247 verified studies (172 RCT, 75 RWE) and 802 rejected -- a 76% rejection rate that shows how important the verification layer is. PubMed contributes the most verified studies at 133, followed by DRKS with 67. The hardest challenge was product name ambiguity. A study might mention "deprexis" only as a comparator in an unrelated trial, and only contextual LLM verification can catch that.

**Timing:** 5:15 -- 6:30

---

## Slide 9: Module -- Stage 3: Analysis and Benchmarking

**Slide title:** Stage 3: Structured Analysis and LLM Benchmarking

**Visual:** `presentation_diagrams/11_module_stage3_analysis.xml`

**Bullets:**

- Full pipeline: 247 verified studies across 60 products (172 RCT, 75 RWE); first deep analysis: 81 studies across 6 products
- 9 fields extracted per study: evidence type, registration ID, sample size, duration, trial arms, outcomes, endpoints, publications, source
- Gold Standard: ~110 manually annotated entries for rigorous P/R/F1 evaluation
- Multi-LLM benchmarking: GPT-4o, Gemini, Claude (commercial) + Llama, Mistral (open-source planned)
- **Achievement:** Provider-agnostic LLM layer; switch between 4 providers with one env variable
- **Challenge:** Deep analysis coverage (6 of 60 products with evidence); metadata normalization across sources
- **Challenge:** Designing fair benchmarking methodology across LLMs with different capabilities and costs

**Speaker notes:**

Stage 3 turns classified evidence into structured, comparable data. The full pipeline verified 247 studies across 60 products -- 172 RCTs and 75 RWE studies. For the first deep analysis, the LLM read 81 studies across 6 products and extracted 9 standardized fields per study. The strongest result so far is "deprexis" as a validation anchor, with 25+ studies providing rich extraction opportunities. The Gold Standard of ~110 manually annotated entries will enable rigorous precision, recall, and F1 scoring at the field level, not just binary classification. The planned multi-LLM benchmarking will compare whether GPT-4o, Gemini, and Claude produce different quality levels, and whether open-source models like Llama can compete.

**Timing:** 6:30 -- 7:30

---

## Slide 10: Timeline and Current Position

**Slide title:** Where I Am in the Thesis Timeline

**Visual:** `presentation_diagrams/4_timeline.xml`

**Bullets:**

- **Completed (Phases 1-3):** literature review, framework design, expose defense, all 5 scrapers, two-layer classification, first German evidence run
- **In progress (Phase 4):** manual gold standard annotation (~110 entries), multi-LLM benchmarking setup
- **Planned (Phase 5):** Pitchbook financial data linkage, US transfer test, open-source LLM benchmarking (Llama, Mistral)
- **Planned (Phase 6):** thesis writing, results analysis, visualizations, defense preparation
- Thesis submission deadline: October 2026

**Speaker notes:**

The current position is at the boundary between implementation and validation. Phases 1 through 3 are complete: the pipeline works end-to-end for German DiGAs and produces structured output. Phase 4 is in progress: I am building a manual gold standard of approximately 110 annotated entries that will serve as the reference for evaluating LLM accuracy. The important shift for May and June is from "can the pipeline work" to "how well does it work compared with a manual reference, and what is its defensible scope." The financial data from Pitchbook and the US market transfer test are planned for Phase 5 but depend on first having validated German results.

**Timing:** 7:30 -- 8:15

---

## Slide 11: Questions for Discussion

**Slide title:** Feedback I Need

**Visual:** `presentation_deck/validation_and_feedback_loop.xml`

**Bullets:**

1. **Validation scope:** Is a rigorous German gold standard sufficient for the main evaluation, with the US market used only as a transfer/generalization test?
2. **Evidence taxonomy:** Is the RCT/RWE binary classification enough for the thesis, or should the schema distinguish observational, registry, usability, economic, and meta-analysis evidence?
3. **Success criterion:** Should I optimize for extraction quality (accuracy per field), catalog coverage (% of products analyzed), or usefulness of the final database for downstream analysis?
4. **Financial linkage depth:** For a master thesis, is descriptive Pitchbook linkage sufficient, or should I test a limited association between evidence strategy and funding outcomes?

**Speaker notes:**

This is the most important slide. I want feedback on the thesis boundaries before I spend the remaining months optimizing the wrong dimension. My current working preference is to make the German validation rigorous with precision/recall/F1 metrics, keep the financial analysis descriptive, and use the US market as a transfer test rather than a second full empirical study. But I would rather align with your expectations now than discover a mismatch during the defense. Any guidance on which of these four questions to prioritize would directly shape how I spend May through August.

**Timing:** 8:15 -- 9:15

---

## Slide 12: Closing

**Slide title:** Next Steps and Thank You

**Bullets -- Next Steps:**

- Complete manual gold standard and run validation metrics (P/R/F1)
- Benchmark across commercial LLMs (GPT-4o, Gemini, Claude) and open-source models (Llama, Mistral)
- Run US transfer test on selected companies
- Integrate Pitchbook financial data for descriptive analysis
- Write and defend thesis by October 2026

**Speaker notes:**

You have now seen each module of the pipeline with its results and challenges. The most important takeaway is that the thesis has a working technical foundation and a concrete dataset to evaluate. The main remaining work is turning the pipeline into a defensible research result with clear validation, bounded claims, and useful analysis. Thank you for your time. I look forward to your feedback, especially on the four questions from the previous slide.

**Timing:** 9:15 -- 10:00

---

## Recommended 10-Minute Flow

| Slide | Topic | Time | Visual |
|-------|-------|------|--------|
| 1 | Title | 0:00 -- 0:30 | -- |
| 2 | Problem Statement | 0:30 -- 1:15 | `1_evidence_landscape.xml` |
| 3 | Research Questions | 1:15 -- 2:00 | -- |
| 4 | Pipeline Overview (Roadmap) | 2:00 -- 2:30 | `2_pipeline_overview.xml` |
| 5 | Module: Input | 2:30 -- 3:00 | `7_module_input.xml` |
| 6 | Module: Stage 0 Metadata | 3:00 -- 4:00 | `8_module_stage0_metadata.xml` |
| 7 | Module: Stage 1 Collection | 4:00 -- 5:15 | `9_module_stage1_collection.xml` |
| 8 | Module: Stage 2 Classification | 5:15 -- 6:30 | `10_module_stage2_classification.xml` |
| 9 | Module: Stage 3 Analysis | 6:30 -- 7:30 | `11_module_stage3_analysis.xml` |
| 10 | Timeline | 7:30 -- 8:15 | `4_timeline.xml` |
| 11 | Discussion Questions | 8:15 -- 9:15 | -- |
| 12 | Closing | 9:15 -- 10:00 | -- |

---

## Backup Slides

Use these only if professors ask for technical detail or if the presentation can run slightly over.

### Backup A: Full System Architecture

**Visual:** `presentation_diagrams/5_full_system_architecture.xml`

**Key points:**

- Python 3.13, asyncio, Click CLI
- Playwright for DRKS/ISRCTN/DiGA scraping (headless Chromium)
- browser-use AI agent for website evidence fallback
- httpx (HTTP/2) for PubMed; curl subprocess for ClinicalTrials.gov
- LangChain abstraction: AzureChatOpenAI, ChatOpenAI, ChatGoogleGenerativeAI, ChatAnthropic
- EvidenceOrchestrator coordinates collect_candidates(), verify_and_classify(), run_website_fallback()
- All raw data preserved for reproducibility (XML, JSON, HTML per study)

### Backup B: Two-Layer Architecture Detail

**Visual:** `presentation_diagrams/3_two_layer_architecture.xml`

**Key points:**

- Detailed view of the classification funnel with prompt templates
- Verification prompt includes: dtx_name, company, ICD codes, study title, abstract, raw page content
- RCT keywords: randomized, controlled trial, double-blind, placebo-controlled, phase II/III
- RWE keywords: observational, retrospective, cohort, real-world, cross-sectional, registry
- Keyword fallback activates when LLM hits rate limits

### Backup C: Validation Metrics Plan

**Key points:**

- Precision, recall, F1 against manual gold standard (~110 annotated entries)
- Field-level accuracy: trial registration IDs, sample size, evidence type, endpoints, outcomes
- Coverage rate: % of DiGA catalog with verified evidence
- Error categories: false positives, missed evidence, wrong product match, incomplete metadata
- Multi-LLM comparison: same gold standard evaluated across GPT-4o, Gemini, Claude, then open-source (Llama, Mistral)

### Backup D: Scope Boundary

**Key points:**

- Main thesis evidence base: German DiGA (76 products, rigorous validation)
- Robustness/transfer test: selected US DTx companies (38 products)
- Financial layer: descriptive Pitchbook linkage unless supervisor feedback asks for stronger modeling
- LLM benchmarking: commercial providers first, open-source as stretch goal with time permitting

---

## Diagram Files Reference

All diagrams are in `presentation_diagrams/`:

| File | Purpose |
|------|---------|
| `1_evidence_landscape.xml` | Problem statement: fragmented evidence sources |
| `2_pipeline_overview.xml` | Full pipeline roadmap (Stages 0-3) |
| `3_two_layer_architecture.xml` | Classification architecture detail |
| `4_timeline.xml` | Thesis timeline and current position |
| `5_full_system_architecture.xml` | Technical architecture (backup) |
| `6_results_funnel.xml` | Evidence funnel visualization |
| `7_module_input.xml` | Module detail: Input and DTx Identification |
| `8_module_stage0_metadata.xml` | Module detail: Metadata Collection |
| `9_module_stage1_collection.xml` | Module detail: Evidence Collection |
| `10_module_stage2_classification.xml` | Module detail: Two-Layer Classification |
| `11_module_stage3_analysis.xml` | Module detail: Analysis and Benchmarking |
