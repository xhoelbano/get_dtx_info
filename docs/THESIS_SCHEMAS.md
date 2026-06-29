# Digital Therapeutics Data Collection System
## Thesis Documentation - Phase 1 & Phase 2

> **Historical document.** Superseded by the documentation hub in `thesis_docs/` (see `thesis_docs/README.md`). Kept for reference; some details below are out of date.

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

## 3. Phase 2: Evidence Discovery (Two-Layer Classification)

### 3.1 Data Sources

| Source | Type | Coverage | Implementation |
|--------|------|----------|----------------|
| **PubMed** | API (E-utilities) | Global research publications | `httpx` async client |
| **ClinicalTrials.gov** | API v2 | US and international trials | `curl` subprocess |
| **DRKS** | Web scraping | German/EU clinical trials | Playwright browser |
| **ISRCTN** | Web scraping | UK/EU/International trials | Playwright browser |

### 3.2 Two-Layer Classification Architecture

The system uses a two-layer approach to minimize false positives:

```
┌─────────────────────────────────────────────────────────────────────────┐
│              Phase 2: Two-Layer Evidence Classification                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 1: Candidate Collection (No Filtering)                        │ │
│  │                                                                      │ │
│  │  SearchQueryGenerator (Deterministic)                                │ │
│  │  ┌────────────────────────────────────────────────────────────────┐ │ │
│  │  │ Query 1: "[DTx name]"           (exact phrase)                  │ │ │
│  │  │ Query 2: "[DTx name]" AND "[Company]"  (with company filter)    │ │ │
│  │  └────────────────────────────────────────────────────────────────┘ │ │
│  │                    │                                                 │ │
│  │     ┌──────────────┼──────────────┬──────────────┐                  │ │
│  │     ▼              ▼              ▼              ▼                  │ │
│  │  ┌────────┐  ┌─────────────┐  ┌────────┐  ┌─────────┐              │ │
│  │  │ PubMed │  │ClinicalTrials│  │  DRKS  │  │ ISRCTN  │              │ │
│  │  │  API   │  │  .gov API   │  │Playwright│ │Playwright│              │ │
│  │  └───┬────┘  └──────┬──────┘  └────┬────┘  └────┬────┘              │ │
│  │      └───────────┬──┴──────────────┴────────────┘                   │ │
│  │                  ▼                                                   │ │
│  │         candidates/{source}/studies.json + raw/                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                   │                                      │
│                                   ▼                                      │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 2: LLM Verification & Classification                          │ │
│  │                                                                      │ │
│  │  EvidenceVerifier (LLM-based)                                        │ │
│  │  ┌────────────────────────────────────────────────────────────────┐ │ │
│  │  │ "Is this study specifically about [DTx name] from [Company]?"   │ │ │
│  │  │ Compares: title, abstract, sponsor, intervention with DTx info  │ │ │
│  │  └────────────────────────────────────────────────────────────────┘ │ │
│  │                    │                                                 │ │
│  │         ┌──────────┴──────────┐                                     │ │
│  │         │                     │                                     │ │
│  │     Relevant              Not Relevant                              │ │
│  │         │                     │                                     │ │
│  │         ▼                     ▼                                     │ │
│  │  EvidenceClassifierV2     rejected/                                 │ │
│  │  ┌────────────────────┐                                             │ │
│  │  │ RCT vs RWE (LLM)   │                                             │ │
│  │  └────────┬───────────┘                                             │ │
│  │      ┌────┴────┐                                                    │ │
│  │      ▼         ▼                                                    │ │
│  │  verified/  verified/                                               │ │
│  │  RCT/       RWE/                                                    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Deterministic Query Generation (NEW)

Replaced LLM-based query generation with deterministic 2-query approach:

```
┌─────────────────────────────────────────────────────────────────┐
│                  Deterministic Query Generation                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Input:                                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ DTx Name: "deprexis"                   │                     │
│  │ Company: "GAIA AG"                     │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  ┌────────────────────────────────────────┐                     │
│  │      SearchQueryGenerator (No LLM)     │                     │
│  │                                         │                     │
│  │  • Clean product name (remove ®™ etc)  │                     │
│  │  • Clean company name (remove GmbH etc)│                     │
│  │  • Generate exactly 2 queries          │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  Output:                                                         │
│  ┌────────────────────────────────────────┐                     │
│  │ Query 1: "deprexis"                    │                     │
│  │ Query 2: "deprexis" AND "GAIA"         │                     │
│  └────────────────────────────────────────┘                     │
│                                                                  │
│  Benefits:                                                       │
│  • Predictable: Same queries every time                         │
│  • Targeted: No generic condition searches                      │
│  • Fast: No LLM call needed                                     │
│  • Auditable: Easy to understand what was searched              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 LLM Relevance Verification (Layer 2)

```
┌─────────────────────────────────────────────────────────────────┐
│                  LLM Relevance Verification                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  For each candidate study:                                       │
│  ┌────────────────────────────────────────┐                     │
│  │ DTx Info:                              │                     │
│  │ • Name: "companion® shoulder"          │                     │
│  │ • Company: "medi GmbH"                 │                     │
│  │ • Description: "Digital shoulder..."   │                     │
│  └────────────────────────────────────────┘                     │
│                          +                                       │
│  ┌────────────────────────────────────────┐                     │
│  │ Study Info:                            │                     │
│  │ • Title: "Kinesiologic considerations" │                     │
│  │ • Abstract: "...companion to another   │                     │
│  │   paper...shoulder rehabilitation..."  │                     │
│  │ • Sponsor: "University of..."          │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  ┌────────────────────────────────────────┐                     │
│  │           EvidenceVerifier             │                     │
│  │                                         │                     │
│  │  Prompt: "Is this study specifically   │                     │
│  │  about 'companion® shoulder' from      │                     │
│  │  'medi GmbH'? The study must EXPLICITLY│                     │
│  │  mention the DTx product by name."     │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  ┌────────────────────────────────────────┐                     │
│  │ Response:                              │                     │
│  │ {                                      │                     │
│  │   "is_relevant": false,                │                     │
│  │   "confidence": 95,                    │                     │
│  │   "reason": "Study does not mention    │                     │
│  │     'companion® shoulder' DTx. Generic │                     │
│  │     shoulder rehabilitation study."    │                     │
│  │ }                                      │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│           ┌──────────────┴──────────────┐                       │
│           ▼                             ▼                       │
│      is_relevant=true              is_relevant=false            │
│           │                             │                       │
│      → Classify RCT/RWE            → Save to rejected/          │
│      → Save to verified/                                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.5 Evidence Classification (RCT vs RWE)

After verification, relevant studies are classified:

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
│  Priority 2: LLM Classification (EvidenceClassifierV2)          │
│  ┌────────────────────────────────────────┐                     │
│  │ Uses both keyword scoring and LLM      │                     │
│  │ for ambiguous cases                    │                     │
│  │                                        │                     │
│  │ RCT Keywords:                          │                     │
│  │ • randomized, randomised, rct          │                     │
│  │ • controlled trial, double-blind       │                     │
│  │ • placebo-controlled, phase ii/iii     │                     │
│  │                                        │                     │
│  │ RWE Keywords:                          │                     │
│  │ • real-world, observational            │                     │
│  │ • retrospective, registry              │                     │
│  │ • cohort study, cross-sectional        │                     │
│  └────────────────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.6 Evidence Folder Structure (Two-Layer)

**New Folder Structure:**
```
evidence/
├── Germany/
│   └── {dtx-slug}/                    # slugified DTx name
│       ├── candidates/                 # LAYER 1: All search results
│       │   ├── pubmed/
│       │   │   ├── studies.json
│       │   │   └── raw/               # Raw XML files
│       │   ├── clinicaltrials/
│       │   │   ├── studies.json
│       │   │   └── raw/               # Raw JSON files
│       │   └── drks/
│       │       ├── studies.json
│       │       └── raw/
│       │
│       ├── verified/                   # LAYER 2: LLM-verified studies
│       │   ├── RCT/
│       │   │   ├── pubmed/studies.json
│       │   │   ├── clinicaltrials/studies.json
│       │   │   └── drks/studies.json
│       │   └── RWE/
│       │       └── {source}/studies.json
│       │
│       └── rejected/                   # False positives (for debugging)
│           └── {source}/rejected.json
│
├── USA/
│   └── {dtx-slug}/
│       └── ... (same structure)
│
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

# === Phase 2: Evidence Finding (Two-Layer) ===
# Full workflow (Layer 1 + Layer 2)
python main.py find-evidence --all                     # All DTx, all sources
python main.py find-evidence --all --country germany   # Germany DTx only
python main.py find-evidence --all --country usa       # USA DTx only
python main.py find-evidence --dtx "deprexis"          # Specific DTx

# Layer-by-layer execution
python main.py find-evidence --all --candidates-only   # Layer 1 only: collect candidates
python main.py find-evidence --all --verify-only       # Layer 2 only: verify existing candidates

# Source-specific
python main.py find-evidence --all --source pubmed     # PubMed only
python main.py find-evidence --all --source clinicaltrials
python main.py find-evidence --all --source drks
python main.py find-evidence --all --source isrctn

# Options
python main.py find-evidence --all --no-pdfs           # Skip PDF downloads
python main.py find-evidence --all --max-results 50    # Limit results
python main.py find-evidence --all --legacy            # Use old single-pass workflow

# === Reports & Status ===
python main.py show-status                     # Show all data status
python main.py evidence-summary                # Generate evidence report
```

### 4.2 Output Files

| File | Purpose | Location |
|------|---------|----------|
| `dtx_data.json` | German DTx metadata | `data/dtx_data.json` |
| `dtx_data_usa.json` | USA DTx metadata | `data/dtx_data_usa.json` |
| `studies.json` | Candidates (Layer 1) | `evidence/{Country}/{DTx}/candidates/{Source}/` |
| `studies.json` | Verified evidence (Layer 2) | `evidence/{Country}/{DTx}/verified/{RCT|RWE}/{Source}/` |
| `rejected.json` | False positives | `evidence/{Country}/{DTx}/rejected/{Source}/` |
| `raw/*.xml` or `*.json` | Raw API responses | `evidence/{Country}/{DTx}/{layer}/{Source}/raw/` |
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

## 6. LLM Provider System

### 6.1 Multi-Provider Architecture

The system supports multiple LLM providers through a centralized abstraction layer, allowing easy switching between providers without code changes.

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM Provider Architecture                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  .env Configuration                                              │
│  ┌────────────────────────────────────────┐                     │
│  │ LLM_PROVIDER=openai                    │  ◄── Switch here    │
│  │ OPENAI_API_KEY=sk-...                  │                     │
│  │ OPENAI_MODEL=gpt-5                     │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│                          ▼                                       │
│  ┌────────────────────────────────────────┐                     │
│  │         LLMProvider.get_llm()          │                     │
│  │    (utils/llm_provider.py)             │                     │
│  └────────────────────────────────────────┘                     │
│                          │                                       │
│         ┌────────────────┼────────────────┐                     │
│         │                │                │                     │
│         ▼                ▼                ▼                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ Azure OpenAI│  │   OpenAI    │  │   Gemini    │             │
│  │ (Default)   │  │ (gpt-4/5)   │  │ (Optional)  │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│                                                                  │
│  ┌─────────────┐                                                │
│  │  Anthropic  │                                                │
│  │ (Optional)  │                                                │
│  └─────────────┘                                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Supported Providers

| Provider | LLM_PROVIDER Value | Required Env Variables | Status |
|----------|-------------------|------------------------|--------|
| **Azure OpenAI** | `azure_openai` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` | Default |
| **OpenAI** | `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` | Supported |
| **Google Gemini** | `gemini` | `GOOGLE_API_KEY`, `GOOGLE_MODEL` | Optional |
| **Anthropic Claude** | `anthropic` | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Optional |

### 6.3 Configuration Example (.env)

```env
# === LLM Provider Selection ===
# Options: azure_openai, openai, gemini, anthropic
LLM_PROVIDER=openai

# === Azure OpenAI Configuration ===
AZURE_OPENAI_API_KEY=your_azure_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-08-01-preview

# === OpenAI Configuration ===
OPENAI_API_KEY=sk-your_openai_key
OPENAI_MODEL=gpt-5

# === Optional: Google Gemini ===
# GOOGLE_API_KEY=your_google_key
# GOOGLE_MODEL=gemini-1.5-pro

# === Optional: Anthropic Claude ===
# ANTHROPIC_API_KEY=your_anthropic_key
# ANTHROPIC_MODEL=claude-3-opus-20240229
```

### 6.4 Reasoning Model Support (GPT-5, o1, o3)

The system automatically detects and handles **reasoning models** (GPT-5, o1, o3, etc.) which use tokens for internal "thinking" before producing output.

**Problem with reasoning models:**
- These models allocate a portion of `max_tokens` to internal reasoning
- With `max_tokens=4000`, the model may use all tokens for reasoning, leaving nothing for output
- Symptom: Empty responses with `finish_reason: length`

**Automatic fix:**
```python
# In utils/llm_provider.py
is_reasoning_model = any(
    indicator in model.lower() 
    for indicator in ["o1", "o3", "gpt-5", "-pro"]
)

if is_reasoning_model:
    # Use at least 16k tokens to account for reasoning overhead
    adjusted_max = max(max_tokens * 4, 16000)
    return ChatOpenAI(
        model=model,
        max_completion_tokens=adjusted_max,  # Newer OpenAI parameter
    )
```

**Example token usage with GPT-5:**
```
completion_tokens: 4137
reasoning_tokens: 3392  (internal thinking)
output_tokens: 745      (actual response)
finish_reason: stop     (completed successfully)
```

### 6.5 Dynamic Source Attribution

The system automatically records which LLM provider and model was used in the output metadata:

```json
{
  "metadata": {
    "source": "LLM Research (OpenAI - gpt-5)",
    "last_updated": "2026-01-27T..."
  }
}
```

This changes dynamically based on `.env` configuration:
- `LLM_PROVIDER=azure_openai` → `"source": "LLM Research (Azure OpenAI - gpt-4o)"`
- `LLM_PROVIDER=openai` → `"source": "LLM Research (OpenAI - gpt-5)"`
- `LLM_PROVIDER=gemini` → `"source": "LLM Research (Google Gemini - gemini-1.5-pro)"`

### 6.6 Files Using LLM Provider

All LLM-dependent modules use the centralized `LLMProvider`:

| File | Purpose |
|------|---------|
| `utils/llm_provider.py` | Central factory (NEW) |
| `utils/translator.py` | German → English translation |
| `utils/search_query_generator.py` | Evidence search query generation |
| `utils/evidence_classifier.py` | RCT vs RWE classification |
| `scrapers/usa_scraper.py` | USA DTx research |

---

## 7. Technology Stack

### 7.1 Core Dependencies

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

### 7.2 External APIs

| API | Purpose | Authentication |
|-----|---------|----------------|
| Azure OpenAI | Translation, Query Generation, Classification | API Key |
| OpenAI | Alternative LLM provider (GPT-4o, GPT-5) | API Key |
| PubMed E-utilities | Paper search and metadata | None (rate limited) |
| Google Play Store | App ratings | None (web scraping) |
| Apple App Store | App ratings | None (web scraping) |

---

## 8. Project Structure

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
│   ├── llm_provider.py              # LLM provider factory (NEW)
│   ├── data_manager.py               # JSON data persistence
│   ├── translator.py                 # LLM translation
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

## 9. Key Design Decisions

### 9.1 Why Playwright over browser-use for DiGA Scraping?
- **Reliability**: browser-use had truncation issues with long responses
- **Control**: Direct DOM manipulation vs LLM interpretation
- **Reproducibility**: Deterministic extraction vs LLM variability

### 9.2 Why LLM for Query Generation?
- **Translation**: German DTx names → English PubMed queries
- **Semantic Understanding**: Converts "Rückenschmerzen" to "back pain"
- **Generalization**: Works for any DTx without hardcoding

### 9.3 Why Two-Stage Relevance Filtering?
- **Speed**: Keyword matching is instant for exact matches
- **Accuracy**: LLM catches papers using different terminology
- **Cost**: Only uses LLM when keyword matching fails

### 9.4 Why Separate RCT from RWE?
- **Regulatory Relevance**: DiGA requires clinical evidence
- **Evidence Hierarchy**: RCTs are gold standard
- **Thesis Analysis**: Compare evidence quality across DTx

---

### 9.5 Why Centralized LLM Provider?
- **Flexibility**: Switch between providers by changing one env variable
- **Cost Control**: Easily switch to cheaper models for development
- **Scalability**: Add new providers without modifying consuming code
- **Reasoning Model Support**: Automatic token limit adjustment for GPT-5, o1, o3

---

## 10. Test Results (January 2026)

### 10.1 Evidence Search Results

| DTx | Country | PubMed | ClinicalTrials | DRKS | ISRCTN | Total RCT | Total RWE |
|-----|---------|--------|----------------|------|--------|-----------|-----------|
| deprexis | Germany | 13 | 10 | 5 | 3 | **24** | **7** |
| Somryst | USA | 11 | 1 | 0 | 5 | **6** | **11** |

### 10.2 System Performance

- **PubMed API**: ~2 seconds per query
- **ClinicalTrials.gov API**: ~3 seconds per query (via curl)
- **DRKS (Playwright)**: ~15 seconds per DTx
- **ISRCTN (Playwright)**: ~10 seconds per DTx

---

## 11. Known Issues & Limitations

### 11.1 Cross-Source Duplicate Studies ⚠️
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

### 11.2 Multi-DTx Company Problem ⚠️
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

### 11.3 Query Too Broad / False Positives ✓ FIXED
**Problem**: LLM-generated queries were sometimes too generic and returned unrelated studies.
- Example: "Beats Medical Parkinson's App" → queries like "Parkinson app" returned 45+ results
- NONE of the results were about the specific DTx, just general Parkinson's app research

**Solution Implemented**:
1. **Stricter query generation**: First query MUST be exact quoted product name (e.g., `"Cara Care"`)
2. **Relevance filtering**: All results filtered by `is_result_relevant()` - requires DTx name in title/abstract
3. **Product name enforcement**: LLM prompt updated to prioritize product-specific queries
4. **Filtered count tracking**: System now reports how many irrelevant results were filtered

**Files changed**:
- `utils/search_query_generator.py` - Updated SYSTEM_PROMPT and `_ensure_exact_name_first()`
- `scrapers/evidence/base_evidence_scraper.py` - Added `is_result_relevant()` method
- All source scrapers updated to use relevance filtering

### 11.4 PDF Download Limitations
**Problem**: Only PubMed Central (PMC) PDFs can be downloaded.
- NCBI PMC now uses JavaScript bot protection (403 errors)
- Fixed by using Europe PMC as alternative source
- Not all articles have open-access PDFs

**Current Status**: ✓ Fixed - Europe PMC used as primary PDF source

**Limitations**:
- Only ~30-40% of PubMed articles have PMC IDs
- Paywalled journals not accessible
- ClinicalTrials.gov, DRKS, ISRCTN don't provide PDFs

### 11.5 Classification Accuracy
**Problem**: LLM-based RCT/RWE classification is ~85-90% accurate.

**Known issues**:
- "Pragmatic trials" sometimes misclassified
- Secondary analyses of RCTs classified as RWE
- Protocol papers classified as RCT (no results yet)

### 11.6 Other Technical Limitations
- **Rate Limiting**: ClinicalTrials.gov occasionally blocks requests
- **Playwright Speed**: Browser scrapers are 5-10x slower than APIs
- **Language Bias**: System prioritizes English publications
- **Incomplete Data**: Some source fields may be empty

---

## 12. Data Quality Validation (TODO)

### 12.1 Completeness Checks
- [ ] **DTx Coverage**: Verify evidence found for all DTx products
- [ ] **Source Coverage**: Check all 4 sources return results for known DTx
- [ ] **Field Population**: Audit % of records with key fields filled

### 12.2 Correctness Validation
- [ ] **Evidence-DTx Matching**: Manually verify 10-20 random evidence items match correct DTx
- [ ] **Classification Accuracy**: Compare LLM classification with manual review for 50 studies
- [ ] **Cross-Source Consistency**: Check same study classified consistently across sources

### 12.3 Manual Validation Dataset
Create a "gold standard" validation set:
```
validation/
├── deprexis_manual.json      # Manually extracted evidence
├── Somryst_manual.json
├── validation_results.json   # Comparison with automated extraction
```

### 12.4 Quality Metrics to Track
| Metric | Target | How to Measure |
|--------|--------|----------------|
| DTx with evidence | >80% | Count DTx folders vs total DTx |
| RCT precision | >90% | Manual review of classified RCTs |
| RWE precision | >85% | Manual review of classified RWE |
| Duplicate rate | <20% | Cross-source title matching |
| PDF success rate | >30% | PDFs downloaded / articles with PMC ID |

---

## 13. Phase 2 Completion Status

### 13.1 Completed ✓
- [x] Multi-source evidence search (PubMed, ClinicalTrials.gov, DRKS, ISRCTN)
- [x] LLM-based query generation
- [x] LLM-based RCT/RWE classification
- [x] PDF download from Europe PMC
- [x] Folder structure by Country/DTx/Type/Source
- [x] Browser context memory leak fix
- [x] Timeout handling for Playwright scrapers
- [x] Error handling for 403 PDF errors
- [x] Query false positive filtering (quoted exact names + relevance check)
- [x] DRKS official JSON download (instead of HTML scraping)
- [x] **Multi-LLM Provider Support** (Azure OpenAI, OpenAI, Gemini, Anthropic)
- [x] **Reasoning model support** (GPT-5, o1, o3 automatic token adjustment)
- [x] **Dynamic source attribution** (records which LLM was used in output)
- [x] **GPT-5.2-pro reasoning block handling** (extracts JSON from reasoning responses)

### 13.2 Known Issues
**Partially addressed:**
- [~] Multi-DTx company evidence attribution (mitigated by relevance filter, but may still occur)

**Acceptable for now:**
- [ ] Cross-source duplicates not removed
- [ ] ~15% classification uncertainty

### 13.3 TODO for Phase 3

#### MEDIUM PRIORITY 🟡
1. **Cross-source deduplication**
   - Match by DOI
   - Fuzzy title matching
   - NCT/PMID cross-references

2. **Multi-DTx company problem (advanced)**
   - 2-layer classification: scrape all DTx of one Provider, then classify per product
   - LLM verification: "Is this study specifically about {DTx_name}?"

3. **Data quality dashboard**
   - Completeness metrics
   - Classification confidence distribution
   - False positive rate tracking
   - Duplicate detection results

4. **Manual validation**
   - Compare with manually extracted data
   - Calculate precision/recall metrics

#### LOW PRIORITY 🟢
5. **Full-text PDF extraction**
   - Parse PDFs with LLM
   - Extract study results, sample size, outcomes

---

## 14. Future Enhancements

### 14.1 Additional Data Sources
- **Cochrane Library**: Systematic reviews
- **Europe PMC**: Additional publication metadata
- **WHO ICTRP**: International trial registry
- **FDA databases**: US regulatory approvals

### 14.2 EU Country Expansion
- France: ANSM digital health directory
- UK: NICE evidence standards framework
- Netherlands: DiGA equivalent programs

### 14.3 Automation
- Scheduled daily/weekly re-scraping
- Change detection for DTx updates
- Email alerts for new evidence

### 14.4 Analysis Features
- Evidence gap analysis
- Publication trend charts
- RCT vs RWE ratio comparisons
- Citation network analysis

---

*Document Version: 2.3*  
*Last Updated: January 27, 2026*  
*Phase 2 Status: COMPLETE (multi-LLM provider support, reasoning model handling)*  
*Author: Xhoel Bano*
