# Digital Therapeutics Data Collection System
## Thesis Documentation - Phase 1 & Phase 2

---

## 1. System Overview

### 1.1 Purpose
Automated data collection system for Digital Therapeutics (DTx) from regulatory directories, with clinical evidence aggregation from scientific databases.

### 1.2 Scope
- **Phase 1**: DTx metadata extraction from German DiGA directory
- **Phase 2**: Clinical evidence discovery from PubMed (RCT/RWE classification)
- **Future**: US FDA, other EU countries

### 1.3 Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DTx Data Collection System                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│  │   Phase 1    │    │   Phase 2    │    │   Phase 3    │               │
│  │  DTx Scraper │───▶│  Evidence    │───▶│  PDF Parser  │               │
│  │              │    │   Finder     │    │  (Future)    │               │
│  └──────┬───────┘    └──────┬───────┘    └──────────────┘               │
│         │                   │                                            │
│         ▼                   ▼                                            │
│  ┌──────────────────────────────────────┐                               │
│  │           Data Storage (JSON)         │                               │
│  │  • dtx_data.json                      │                               │
│  │  • evidence_metadata.json             │                               │
│  └──────────────────────────────────────┘                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 1: DTx Directory Scraping

### 2.1 Data Source
- **URL**: https://diga.bfarm.de/de/verzeichnis
- **Country**: Germany
- **Regulatory Body**: BfArM (Bundesinstitut für Arzneimittel und Medizinprodukte)

### 2.2 Scraping Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Phase 1: DiGA Scraper Pipeline                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Step 1: List Extraction          Step 2: Detail Extraction         │
│  ┌────────────────────┐          ┌────────────────────┐             │
│  │  DiGA Directory    │          │  Individual DiGA   │             │
│  │  (Main List Page)  │─────────▶│  Detail Pages      │             │
│  └────────┬───────────┘          └────────┬───────────┘             │
│           │                               │                          │
│           ▼                               ▼                          │
│  ┌────────────────────┐          ┌────────────────────┐             │
│  │  Playwright        │          │  Playwright        │             │
│  │  • Scroll to load  │          │  • Click "Mehr     │             │
│  │  • Extract 76 DTx  │          │    anzeigen"       │             │
│  │  • Get URLs        │          │  • Extract all     │             │
│  └────────────────────┘          │    fields          │             │
│                                  └────────────────────┘             │
│                                                                      │
│  Step 3: App Store Enrichment    Step 4: Translation                │
│  ┌────────────────────┐          ┌────────────────────┐             │
│  │  Google Play &     │          │  Azure OpenAI      │             │
│  │  Apple App Store   │─────────▶│  Translation       │             │
│  │  • Ratings         │          │  DE → EN           │             │
│  │  • Review counts   │          └────────────────────┘             │
│  └────────────────────┘                                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 DTx Data Schema (Unified)

Both Germany (`dtx_data.json`) and USA (`dtx_data_usa.json`) use the same schema structure.
The only difference is the price field: `price_eur` for Germany, `price_usd` for USA.

```json
{
  "metadata": {
    "country": "string",           // "Germany" | "USA"
    "last_updated": "ISO8601",     // "2026-01-21T19:02:04.127667Z"
    "total_count": "integer",      // Total DTx count
    "source": "string"             // Data source description
  },
  "dtx_list": [
    {
      // === Core Identification ===
      "dtx_name": "string",            // Product name
      "source_url": "string",          // Source URL (DiGA directory or company website)
      "last_scraped": "ISO8601",       // Scrape timestamp
      
      // === Company Information ===
      "company_provider": "string",    // Company name
      "company_website": "string|null", // Company URL
      "company_founding_year": "integer|null",
      
      // === Regulatory Status ===
      "listing_status": "string",      // "Permanently listed" | "Active" | "Delisted" etc.
      "date_of_first_listing": "string|null", // "YYYY-MM-DD"
      "reason_for_delisting": "string|null",
      
      // === Clinical Information ===
      "clinical_area_icd10": ["string"], // Array of ICD-10 codes
      "dtx_category": "string|null",
      "description": "string",         // Detailed description
      
      // === Platform Availability ===
      "app_store_url": "string|null",
      "play_store_url": "string|null",
      "web_app_url": "string|null",
      
      // === Pricing (country-specific) ===
      "price_eur": "string|null",      // Germany only
      "price_usd": "string|null",      // USA only
      
      // === Languages ===
      "languages": ["string"],         // e.g., ["Deutsch"] or ["English"]
      
      // === Clinical Trials ===
      "trial_registration_ids": ["string"], // NCT numbers
      
      // === App Store Metrics ===
      "reviews_playstore": {
        "rating": "float|null",        // 1.0-5.0
        "review_count": "integer|null",
        "url": "string"
      } | null,
      "reviews_appstore": {
        "rating": "float|null",
        "review_count": "integer|null", 
        "url": "string"
      } | null
    }
  ]
}
```

**Note**: Country-specific fields that were removed for consistency:
- Germany: `dtx_name_de`, `listing_status_de` (German translations)
- USA: `fda_clearance`, `fda_clearance_number`, `clinical_indications` (regulatory details)

### 2.4 Listing Status Classification

| German Status | English Status | Count | Description |
|--------------|----------------|-------|-------------|
| Dauerhaft aufgenommen | Permanently listed | 48 | Full approval with proven efficacy |
| Vorläufig aufgenommen | Provisionally listed | 12 | Conditional approval, evidence pending |
| Aus dem Verzeichnis gestrichen | Delisted | 16 | Removed from directory |

### 2.5 Technologies Used

| Component | Technology | Purpose |
|-----------|------------|---------|
| Browser Automation | Playwright | JavaScript-heavy page rendering |
| HTTP Client | httpx | API calls, app store scraping |
| Translation | Azure OpenAI GPT-4o | German → English translation |
| Data Storage | JSON | Structured data persistence |
| CLI | Click | Command-line interface |

---

## 3. Phase 2: Evidence Discovery (Multi-Source)

### 3.1 Data Sources

| Source | Type | Coverage | Implementation |
|--------|------|----------|----------------|
| **PubMed** | API (E-utilities) | Global research publications | `httpx` async client |
| **ClinicalTrials.gov** | API v2 | US and international trials | `curl` subprocess |
| **DRKS** | Web scraping | German/EU clinical trials | Playwright browser |
| **ISRCTN** | Web scraping | UK/EU/International trials | Playwright browser |

### 3.2 Evidence Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   Phase 2: Evidence Discovery Pipeline                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                      ┌─────────────────────┐                            │
│                      │  EvidenceOrchestrator│                            │
│                      │  (Coordinator)       │                            │
│                      └──────────┬──────────┘                            │
│                                 │                                        │
│         ┌───────────────────────┼───────────────────────┐               │
│         │                       │                       │               │
│  ┌──────▼──────┐    ┌──────────▼──────────┐    ┌──────▼──────┐        │
│  │ SearchQuery │    │  EvidenceClassifier │    │   DataMgr   │        │
│  │ Generator   │    │  (Azure GPT)        │    │             │        │
│  │ (Azure GPT) │    │  RCT vs RWE         │    │             │        │
│  └──────┬──────┘    └──────────┬──────────┘    └─────────────┘        │
│         │                      │                                        │
│         ▼                      ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     Evidence Scrapers                            │   │
│  ├──────────┬──────────────┬─────────────┬─────────────────────────┤   │
│  │ PubMed   │ ClinicalTrials│   DRKS     │      ISRCTN             │   │
│  │ (API)    │ .gov (curl)  │(Playwright) │   (Playwright)          │   │
│  │          │              │             │                          │   │
│  │ • Search │ • Search     │ • Form fill │ • Cookie consent        │   │
│  │ • Details│ • Nested JSON│ • JS render │ • Form submission       │   │
│  │ • PDFs   │              │ • Details   │ • Detail extraction     │   │
│  └──────────┴──────────────┴─────────────┴─────────────────────────┘   │
│                                 │                                        │
│                                 ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    Folder Structure Output                        │   │
│  │  evidence/{Country}/{DTx}/{RCT|RWE}/{Source}/studies.json        │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 LLM-Based Query Generation

The system uses Azure OpenAI to generate intelligent PubMed search queries:

```
┌─────────────────────────────────────────────────────────────────┐
│                  LLM Query Generation Process                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Input:                                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ DTx Name: "Kaia Rückenschmerzen"       │                     │
│  │ Company: "Kaia health software GmbH"   │                     │
│  │ ICD-10: ["M54.5", "M54.4"]            │                     │
│  │ Description: "Digital back pain..."   │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  ┌────────────────────────────────────────┐                     │
│  │           Azure OpenAI GPT-4o          │                     │
│  │  • Translates German → English         │                     │
│  │  • Understands clinical context        │                     │
│  │  • Generates 3-4 targeted queries      │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  Output:                                                         │
│  ┌────────────────────────────────────────┐                     │
│  │ ["Kaia Back Pain app",                 │                     │
│  │  "Kaia health back pain",              │                     │
│  │  "Kaia Back Pain non-specific low      │                     │
│  │   back pain",                          │                     │
│  │  "Kaia health software back pain"]     │                     │
│  └────────────────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 Relevance Filtering (Two-Stage)

```
┌─────────────────────────────────────────────────────────────────┐
│                  Two-Stage Relevance Filtering                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Stage 1: Keyword Matching (Fast)                               │
│  ┌────────────────────────────────────────┐                     │
│  │ Check if DTx identifier appears in     │                     │
│  │ paper title or abstract                │                     │
│  │                                        │                     │
│  │ Identifier: "kaia rückenschmerzen"     │                     │
│  │ Paper text: "...Kaia app for back..."  │                     │
│  │ Result: NO MATCH → Go to Stage 2       │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  Stage 2: LLM Verification (Accurate)                           │
│  ┌────────────────────────────────────────┐                     │
│  │ Prompt: "Is this paper specifically    │                     │
│  │ about the digital therapeutic app      │                     │
│  │ 'Kaia Rückenschmerzen'?"               │                     │
│  │                                        │                     │
│  │ Paper: "Medical App Treatment of       │                     │
│  │ Non-Specific Low Back Pain...Kaia App" │                     │
│  │                                        │                     │
│  │ LLM Response: "yes"                    │                     │
│  │ Result: KEEP                           │                     │
│  └────────────────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.5 Evidence Classification (RCT vs RWE)

```
┌─────────────────────────────────────────────────────────────────┐
│                 Evidence Type Classification                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Priority 1: PubMed Publication Types                           │
│  ┌────────────────────────────────────────┐                     │
│  │ If PublicationType contains:           │                     │
│  │ • "Randomized Controlled Trial" → RCT  │                     │
│  │ • "Clinical Trial, Phase II/III" → RCT │                     │
│  │ • "Observational Study" → RWE          │                     │
│  │ • "Cohort Study" → RWE                 │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  Priority 2: Keyword Scoring                                    │
│  ┌────────────────────────────────────────┐                     │
│  │ RCT Keywords:                          │                     │
│  │ • randomized, randomised, rct          │                     │
│  │ • controlled trial, double-blind       │                     │
│  │ • placebo-controlled, phase ii/iii     │                     │
│  │                                        │                     │
│  │ RWE Keywords:                          │                     │
│  │ • real-world, observational            │                     │
│  │ • retrospective, registry              │                     │
│  │ • cohort study, cross-sectional        │                     │
│  │ • pragmatic trial, naturalistic        │                     │
│  │                                        │                     │
│  │ Score: RCT=3, RWE=1 → Classify as RCT  │                     │
│  └────────────────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.6 Evidence Data Schema (New Multi-Source Format)

**Folder Structure:**
```
evidence/
├── Germany/
│   └── {dtx_name}/              # slugified DTx name
│       ├── RCT/
│       │   ├── pubmed/studies.json
│       │   ├── clinicaltrials/studies.json
│       │   ├── drks/studies.json
│       │   └── isrctn/studies.json
│       └── RWE/
│           └── {source}/studies.json
├── USA/
│   └── {dtx_name}/
│       └── ... (same structure)
└── summary/
    ├── germany_evidence_summary.json
    ├── usa_evidence_summary.json
    └── overall_statistics.json
```

**studies.json Schema:**
```json
{
  "studies": [
    {
      "study_id": "NCT01234567 | PMID12345678 | DRKS00012345 | ISRCTN12345678",
      "title": "Study title",
      "scientific_title": "Full scientific title",
      
      // Source-specific IDs
      "pmid": "12345678",
      "pmc_id": "PMC1234567",
      "nct_id": "NCT01234567",
      "drks_id": "DRKS00012345",
      "isrctn_id": "ISRCTN12345678",
      "doi": "10.1000/xyz123",
      
      // Study Design
      "study_type": "Interventional | Observational",
      "study_design": "Randomized, double-blind, placebo-controlled",
      "phase": "Phase 2/3 | N/A",
      "allocation": "Randomized | Non-randomized",
      
      // Status
      "status": "Completed | Recruiting | Active",
      "recruitment_status": "No longer recruiting",
      "overall_status": "Completed",
      
      // Participants
      "enrollment": "500",
      "participant_info": {
        "type": "Patient | Healthy volunteer",
        "age_group": "Adult",
        "lower_age": "18 Years",
        "sex": "All"
      },
      
      // Clinical Info
      "conditions": ["F32.1", "F33.0"],
      "health_conditions": "Major Depressive Disorder",
      "intervention": "Digital CBT app vs waitlist control",
      
      // Outcomes
      "primary_outcome": "Change in PHQ-9 score at 8 weeks",
      "secondary_outcomes": "Quality of life, adherence",
      
      // Sponsors
      "sponsor": "Company Name",
      "funder": "Funding Organization",
      
      // Geography & Dates
      "countries": ["Germany", "United Kingdom"],
      "start_date": "2020-01-01",
      "completion_date": "2022-06-30",
      
      // Content
      "abstract": "Background: ... Methods: ... Results: ...",
      "brief_summary": "Plain language summary",
      
      // Results
      "has_results": true,
      "publication_links": ["https://pubmed.ncbi.nlm.nih.gov/12345678/"],
      
      // LLM Classification
      "classification": {
        "classification": "RCT | RWE",
        "confidence": 90,
        "reason": "Randomized controlled trial with placebo comparison"
      },
      
      // Metadata
      "source": "PubMed | ClinicalTrials.gov | DRKS | ISRCTN",
      "url": "https://..."
    }
  ],
  "count": 10,
  "queries_used": ["deprexis", "deprexis depression"],
  "_saved_at": "2026-01-27T17:22:32.718917Z",
  "_source": "pubmed"
}
```

---

## 4. CLI Commands

### 4.1 Available Commands

```bash
# === Phase 1: DTx Scraping ===
python main.py scrape-dtx --mode full          # Full scrape of German DiGA
python main.py scrape-dtx --mode incremental   # Update only changed entries
python main.py scrape-dtx --list-only          # Scrape list without details
python main.py scrape-reviews                   # Add app store ratings

# === USA DTx Research ===
python main.py scrape-usa --csv data-format/us_company.csv  # Research US companies
python main.py research-company "Pear Therapeutics"          # Single company

# === Phase 2: Evidence Finding ===
python main.py find-evidence --all                     # All DTx, all sources
python main.py find-evidence --all --country germany   # Germany DTx only
python main.py find-evidence --all --country usa       # USA DTx only
python main.py find-evidence --dtx "deprexis"          # Specific DTx
python main.py find-evidence --all --source pubmed     # PubMed only
python main.py find-evidence --all --source clinicaltrials
python main.py find-evidence --all --source drks
python main.py find-evidence --all --source isrctn
python main.py find-evidence --all --no-pdfs           # Skip PDF downloads
python main.py find-evidence --all --max-results 20    # Limit results

# === Reports & Status ===
python main.py show-status                     # Show all data status
python main.py evidence-summary                # Generate evidence report
```

### 4.2 Output Files

| File | Purpose | Location |
|------|---------|----------|
| `dtx_data.json` | German DTx metadata | `data/dtx_data.json` |
| `dtx_data_usa.json` | USA DTx metadata | `data/dtx_data_usa.json` |
| `studies.json` | Evidence per source | `evidence/{Country}/{DTx}/{Type}/{Source}/` |
| `overall_statistics.json` | Summary stats | `evidence/summary/` |

---

## 5. Data Statistics (Germany - January 2026)

### 5.1 DTx Directory Statistics

| Metric | Count |
|--------|-------|
| **Total DiGA** | 76 |
| Permanently listed | 48 (63%) |
| Provisionally listed | 12 (16%) |
| Delisted | 16 (21%) |

### 5.2 App Store Coverage

| Store | DTx with URLs | Ratings Extracted |
|-------|---------------|-------------------|
| Google Play Store | 59 | 52 (88%) |
| Apple App Store | 58 | 49 (84%) |

### 5.3 Clinical Areas (Top ICD-10 Categories)

| ICD Category | Description | Count |
|--------------|-------------|-------|
| F32-F33 | Depression | 8 |
| F41 | Anxiety disorders | 6 |
| M54 | Back pain | 5 |
| G47 | Sleep disorders | 4 |
| E11 | Type 2 diabetes | 4 |
| K58 | Irritable bowel syndrome | 2 |

---

## 6. Technology Stack

### 6.1 Core Dependencies

```
# Core
python-dotenv>=1.0.0          # Environment management
langchain-openai>=0.1.0       # Azure OpenAI integration

# Browser Automation
playwright>=1.40.0            # Web scraping (primary)
browser-use>=0.1.0            # AI-driven browser (experimental)

# HTTP & Parsing
httpx>=0.27.0                 # Async HTTP client
lxml>=5.0.0                   # XML parsing (PubMed API)

# CLI
click>=8.0.0                  # Command-line interface

# Utilities
python-slugify>=8.0.0         # Text slugification
```

### 6.2 External APIs

| API | Purpose | Authentication |
|-----|---------|----------------|
| Azure OpenAI | Translation, Query Generation, Relevance Check | API Key |
| PubMed E-utilities | Paper search and metadata | None (rate limited) |
| Google Play Store | App ratings | None (web scraping) |
| Apple App Store | App ratings | None (web scraping) |

---

## 7. Project Structure

```
get_dtx_info/
├── main.py                    # CLI entry point
├── requirements.txt           # Dependencies
├── .env                       # API keys (not committed)
│
├── config/
│   ├── germany.json           # German DiGA configuration
│   └── usa.json               # USA DTx configuration
│
├── scrapers/
│   ├── __init__.py
│   ├── base_scraper.py        # Abstract base class
│   ├── diga_scraper.py        # German DiGA scraper
│   ├── usa_scraper.py         # USA DTx LLM researcher
│   ├── app_store_scraper.py   # Play Store/App Store scraper
│   │
│   └── evidence/              # Multi-source evidence system
│       ├── __init__.py
│       ├── base_evidence_scraper.py    # Base class for all evidence scrapers
│       ├── pubmed_scraper.py           # PubMed E-utilities API
│       ├── clinicaltrials_scraper.py   # ClinicalTrials.gov API v2
│       ├── drks_scraper.py             # DRKS Playwright scraper
│       ├── isrctn_scraper.py           # ISRCTN Playwright scraper
│       └── evidence_orchestrator.py    # Coordinator for all sources
│
├── utils/
│   ├── __init__.py
│   ├── data_manager.py               # JSON data persistence
│   ├── translator.py                 # Azure OpenAI translation
│   ├── search_query_generator.py     # LLM query generation
│   └── evidence_classifier.py        # LLM RCT/RWE classification
│
├── data/
│   ├── dtx_data.json          # German DTx data
│   └── dtx_data_usa.json      # USA DTx data
│
├── evidence/                  # Evidence output (NEW)
│   ├── Germany/
│   │   └── {dtx_name}/
│   │       ├── RCT/{source}/studies.json
│   │       └── RWE/{source}/studies.json
│   ├── USA/
│   │   └── {dtx_name}/...
│   └── summary/
│       └── overall_statistics.json
│
├── data-format/
│   ├── dtx.json               # DTx schema template
│   └── evidence.json          # Evidence schema template
│
└── docs/
    ├── THESIS_SCHEMAS.md      # This documentation
    └── EVIDENCE_SYSTEM.md     # Evidence system documentation
```

---

## 8. Key Design Decisions

### 8.1 Why Playwright over browser-use for DiGA Scraping?
- **Reliability**: browser-use had truncation issues with long responses
- **Control**: Direct DOM manipulation vs LLM interpretation
- **Reproducibility**: Deterministic extraction vs LLM variability

### 8.2 Why LLM for Query Generation?
- **Translation**: German DTx names → English PubMed queries
- **Semantic Understanding**: Converts "Rückenschmerzen" to "back pain"
- **Generalization**: Works for any DTx without hardcoding

### 8.3 Why Two-Stage Relevance Filtering?
- **Speed**: Keyword matching is instant for exact matches
- **Accuracy**: LLM catches papers using different terminology
- **Cost**: Only uses LLM when keyword matching fails

### 8.4 Why Separate RCT from RWE?
- **Regulatory Relevance**: DiGA requires clinical evidence
- **Evidence Hierarchy**: RCTs are gold standard
- **Thesis Analysis**: Compare evidence quality across DTx

---

## 9. Test Results (January 2026)

### 9.1 Evidence Search Results

| DTx | Country | PubMed | ClinicalTrials | DRKS | ISRCTN | Total RCT | Total RWE |
|-----|---------|--------|----------------|------|--------|-----------|-----------|
| deprexis | Germany | 13 | 10 | 5 | 3 | **24** | **7** |
| Somryst | USA | 11 | 1 | 0 | 5 | **6** | **11** |

### 9.2 System Performance

- **PubMed API**: ~2 seconds per query
- **ClinicalTrials.gov API**: ~3 seconds per query (via curl)
- **DRKS (Playwright)**: ~15 seconds per DTx
- **ISRCTN (Playwright)**: ~10 seconds per DTx

---

## 10. Known Issues & Limitations

### 10.1 Cross-Source Duplicate Studies ⚠️
**Problem**: The same study may appear in multiple sources with different IDs:
- A PubMed article (PMID) may reference a ClinicalTrials.gov trial (NCT)
- An ISRCTN trial may be cross-registered in DRKS
- The same RCT appears as both a trial registration AND a publication

**Impact**: Evidence counts may be inflated. A single RCT could be counted 2-4 times.

**Current Workaround**: Deduplication only within each source (by study_id).

**TODO**: Implement cross-source deduplication using:
- DOI matching
- Title similarity (fuzzy matching)
- Cross-reference IDs (NCT mentioned in PubMed abstract)
- Author + year matching

### 10.2 Multi-DTx Company Problem ⚠️
**Problem**: When one company has multiple DTx products:
- Query generation uses company name (e.g., "GAIA AG")
- This returns evidence for ALL GAIA products, not just the target DTx
- Example: Searching for "elevida" also finds "deprexis" papers (both from GAIA)

**Impact**: Evidence may be incorrectly attributed to the wrong DTx from the same company.

**Examples of affected companies**:
- GAIA AG: deprexis, elevida, velibra, vorvida
- Kaia Health: Kaia Rückenschmerzen, Kaia COPD, Kaia Arthrose
- Oviva: Oviva Direkt für Adipositas, Oviva Direkt Bluthochdruck, Oviva Direkt Diabetes

**TODO**: Improve query generation to:
- Prioritize product-specific queries over company queries
- Add LLM relevance filtering: "Is this study specifically about {DTx_name}?"
- Use DTx description/indication to filter results

### 10.3 Query Too Broad / False Positives ⚠️ (CRITICAL)
**Problem**: LLM-generated queries are sometimes too generic and return unrelated studies.
- Example: "Beats Medical Parkinson's App" → queries like "Parkinson app" return 45+ results
- NONE of the results are about the specific DTx, just general Parkinson's app research
- Current relevance filtering not catching these false positives

**Impact**: 
- Large number of irrelevant studies polluting the dataset
- High false positive rate for less-known DTx products
- Manual cleanup required

**Observed cases**:
- "Beats Medical Parkinson's App" → generic Parkinson's app studies
- Likely affects many smaller/newer DTx products

**TODO** (HIGH PRIORITY):
1. **Stricter query generation**: Force LLM to generate product-specific queries only
2. **Mandatory relevance verification**: Run LLM check on EVERY result, not just fallback
3. **Product name requirement**: At least one query must include exact product name
4. **Confidence threshold**: Reject results where product name not mentioned in title/abstract
5. **Max results cap**: Limit to 10-15 per query to avoid flooding with irrelevant studies

### 10.4 PDF Download Limitations
**Problem**: Only PubMed Central (PMC) PDFs can be downloaded.
- NCBI PMC now uses JavaScript bot protection (403 errors)
- Fixed by using Europe PMC as alternative source
- Not all articles have open-access PDFs

**Current Status**: ✓ Fixed - Europe PMC used as primary PDF source

**Limitations**:
- Only ~30-40% of PubMed articles have PMC IDs
- Paywalled journals not accessible
- ClinicalTrials.gov, DRKS, ISRCTN don't provide PDFs

### 10.5 Classification Accuracy
**Problem**: LLM-based RCT/RWE classification is ~85-90% accurate.

**Known issues**:
- "Pragmatic trials" sometimes misclassified
- Secondary analyses of RCTs classified as RWE
- Protocol papers classified as RCT (no results yet)

### 10.6 Other Technical Limitations
- **Rate Limiting**: ClinicalTrials.gov occasionally blocks requests
- **Playwright Speed**: Browser scrapers are 5-10x slower than APIs
- **Language Bias**: System prioritizes English publications
- **Incomplete Data**: Some source fields may be empty

---

## 11. Data Quality Validation (TODO)

### 11.1 Completeness Checks
- [ ] **DTx Coverage**: Verify evidence found for all DTx products
- [ ] **Source Coverage**: Check all 4 sources return results for known DTx
- [ ] **Field Population**: Audit % of records with key fields filled

### 11.2 Correctness Validation
- [ ] **Evidence-DTx Matching**: Manually verify 10-20 random evidence items match correct DTx
- [ ] **Classification Accuracy**: Compare LLM classification with manual review for 50 studies
- [ ] **Cross-Source Consistency**: Check same study classified consistently across sources

### 11.3 Manual Validation Dataset
Create a "gold standard" validation set:
```
validation/
├── deprexis_manual.json      # Manually extracted evidence
├── Somryst_manual.json
├── validation_results.json   # Comparison with automated extraction
```

### 11.4 Quality Metrics to Track
| Metric | Target | How to Measure |
|--------|--------|----------------|
| DTx with evidence | >80% | Count DTx folders vs total DTx |
| RCT precision | >90% | Manual review of classified RCTs |
| RWE precision | >85% | Manual review of classified RWE |
| Duplicate rate | <20% | Cross-source title matching |
| PDF success rate | >30% | PDFs downloaded / articles with PMC ID |

---

## 12. Phase 2 Completion Status

### 12.1 Completed ✓
- [x] Multi-source evidence search (PubMed, ClinicalTrials.gov, DRKS, ISRCTN)
- [x] LLM-based query generation
- [x] LLM-based RCT/RWE classification
- [x] PDF download from Europe PMC
- [x] Folder structure by Country/DTx/Type/Source
- [x] Browser context memory leak fix
- [x] Timeout handling for Playwright scrapers
- [x] Error handling for 403 PDF errors

### 12.2 Known Issues
**Must Fix (Phase 3):**
- [ ] ⚠️ Query too broad → high false positive rate (e.g., Beats Medical case)
- [ ] ⚠️ Multi-DTx company evidence attribution

**Acceptable for now:**
- [ ] Cross-source duplicates not removed

### 12.3 TODO for Phase 3

#### HIGH PRIORITY 🔴
1. **Fix false positive problem (Query too broad)**
   - Stricter query generation: product name MUST appear
   - Mandatory LLM relevance check on ALL results (not just fallback)
   - Reject results where product name not in title/abstract
   - Cap results per query (10-15 max)
   - Re-run evidence search after fixing

   TRY - 2 layer classification - scrape all dtx of one Provider and then classify


2. **Evidence-DTx relevance filtering**
   - LLM verification: "Is this study specifically about {DTx_name}?"
   - Filter out company-level evidence
   - Filter out generic condition research

#### MEDIUM PRIORITY 🟡
3. **Cross-source deduplication**
   - Match by DOI
   - Fuzzy title matching
   - NCT/PMID cross-references

4. **Data quality dashboard**
   - Completeness metrics
   - Classification confidence distribution
   - False positive rate tracking
   - Duplicate detection results

5. **Manual validation**
   - Compare with manually extracted data
   - Calculate precision/recall metrics

#### LOW PRIORITY 🟢
6. **Full-text PDF extraction**
   - Parse PDFs with LLM
   - Extract study results, sample size, outcomes

---

## 13. Future Enhancements

### 13.1 Additional Data Sources
- **Cochrane Library**: Systematic reviews
- **Europe PMC**: Additional publication metadata
- **WHO ICTRP**: International trial registry
- **FDA databases**: US regulatory approvals

### 13.2 EU Country Expansion
- France: ANSM digital health directory
- UK: NICE evidence standards framework
- Netherlands: DiGA equivalent programs

### 13.3 Automation
- Scheduled daily/weekly re-scraping
- Change detection for DTx updates
- Email alerts for new evidence

### 13.4 Analysis Features
- Evidence gap analysis
- Publication trend charts
- RCT vs RWE ratio comparisons
- Citation network analysis

---

*Document Version: 2.1*  
*Last Updated: January 27, 2026*  
*Phase 2 Status: COMPLETE (with known limitations)*  
*Author: Xhoel Bano*
