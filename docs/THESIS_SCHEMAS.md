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

### 2.3 DTx Data Schema

```json
{
  "metadata": {
    "country": "string",           // "Germany"
    "last_updated": "ISO8601",     // "2026-01-21T19:02:04.127667Z"
    "total_count": "integer",      // 76
    "active_count": "integer",     // 48
    "provisional_count": "integer", // 12
    "delisted_count": "integer"    // 16
  },
  "dtx_list": [
    {
      // === Core Identification ===
      "dtx_name": "string",            // Full name (may include German)
      "dtx_name_de": "string",         // Original German name
      "source_url": "string",          // DiGA directory URL
      "last_scraped": "ISO8601",       // Scrape timestamp
      
      // === Company Information ===
      "company_provider": "string",    // Company name with country
      "company_website": "string|null", // Company URL
      "company_founding_year": "integer|null",
      
      // === Regulatory Status ===
      "listing_status": "string",      // "Permanently listed" | "Provisionally listed" | "Delisted"
      "listing_status_de": "string",   // "Dauerhaft aufgenommen" | "Vorläufig aufgenommen" | "Aus dem Verzeichnis gestrichen"
      "date_of_first_listing": "string|null", // "YYYY-MM-DD"
      "reason_for_delisting": "string|null",
      
      // === Clinical Information ===
      "clinical_area_icd10": ["string"], // Array of ICD-10 codes
      "dtx_category": "string|null",
      "description": "string",         // Detailed description (translated to EN)
      
      // === Platform Availability ===
      "app_store_url": "string|null",
      "play_store_url": "string|null",
      "web_app_url": "string|null",
      
      // === Pricing & Languages ===
      "price_eur": "string",           // e.g., "551.70"
      "languages": ["string"],         // e.g., ["Deutsch"]
      
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

## 3. Phase 2: Evidence Discovery

### 3.1 Data Sources
- **PubMed** (E-utilities API) - Primary source, free, reliable
- **Google Scholar** (browser-use) - Optional, often blocked

### 3.2 Evidence Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   Phase 2: Evidence Discovery Pipeline                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────┐          │
│  │  DTx Data   │───▶│  LLM Query      │───▶│  PubMed API     │          │
│  │  (Input)    │    │  Generation     │    │  Search         │          │
│  └─────────────┘    └─────────────────┘    └────────┬────────┘          │
│                                                      │                   │
│                                                      ▼                   │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    Post-Processing Pipeline                      │    │
│  ├─────────────────────────────────────────────────────────────────┤    │
│  │                                                                  │    │
│  │  Step 1: Deduplication      Step 2: Relevance Filter            │    │
│  │  ┌──────────────────┐       ┌──────────────────┐                │    │
│  │  │ Remove duplicate │──────▶│ Keyword Match OR │                │    │
│  │  │ papers by title  │       │ LLM Verification │                │    │
│  │  └──────────────────┘       └────────┬─────────┘                │    │
│  │                                      │                          │    │
│  │                                      ▼                          │    │
│  │  Step 3: Classification     Step 4: Storage                     │    │
│  │  ┌──────────────────┐       ┌──────────────────┐                │    │
│  │  │ RCT vs RWE       │──────▶│ evidence_        │                │    │
│  │  │ (PubType+Keywords)│       │ metadata.json    │                │    │
│  │  └──────────────────┘       └──────────────────┘                │    │
│  │                                                                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
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

### 3.6 Evidence Data Schema

```json
{
  "metadata": {
    "last_updated": "ISO8601",
    "total_rct": "integer",
    "total_rwe": "integer"
  },
  "evidence_by_dtx": {
    "[DTx Name]": {
      "search_date": "ISO8601",
      "queries_used": ["string"],
      "RCT": [
        {
          "title": "string",
          "authors": "string",           // "LastName FirstName et al."
          "publication_year": "string",
          "journal": "string",
          "pmid": "string",              // PubMed ID
          "doi": "string|null",
          "abstract": "string",          // Truncated to 1000 chars
          "publication_types": ["string"],
          "source": "string",            // "PubMed"
          "url": "string",               // PubMed URL
          "evidence_type": "string",     // "RCT"
          "dtx_name": "string"
        }
      ],
      "RWE": [
        // Same structure as RCT
      ]
    }
  }
}
```

---

## 4. CLI Commands

### 4.1 Available Commands

```bash
# Phase 1: DTx Scraping
python main.py scrape-diga --all              # Scrape all 76 DiGA entries
python main.py scrape-diga --dtx "Kaia"       # Scrape specific DTx
python main.py scrape-reviews                  # Add app store ratings

# Phase 2: Evidence Finding  
python main.py find-evidence --all --skip-scholar    # Find evidence for all DTx
python main.py find-evidence --dtx "Kaia COPD"       # Find evidence for specific DTx

# Status
python main.py show-status                     # Show data collection status
```

### 4.2 Output Files

| File | Purpose | Location |
|------|---------|----------|
| `dtx_data.json` | DTx metadata | `data/dtx_data.json` |
| `evidence_metadata.json` | Evidence papers | `data/evidence_metadata.json` |

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
│   └── germany.json           # Country-specific configuration
│
├── scrapers/
│   ├── __init__.py
│   ├── base_scraper.py        # Abstract base class
│   ├── diga_scraper.py        # German DiGA scraper
│   ├── evidence_scraper.py    # PubMed/Scholar evidence finder
│   └── app_store_scraper.py   # Play Store/App Store scraper
│
├── utils/
│   ├── __init__.py
│   └── data_manager.py        # JSON data persistence
│
├── data/
│   ├── dtx_data.json          # Scraped DTx data
│   └── evidence_metadata.json # Evidence papers
│
├── data-format/
│   ├── dtx.json               # DTx schema template
│   └── evidence.json          # Evidence schema template
│
└── docs/
    └── THESIS_SCHEMAS.md      # This documentation
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

## 9. Limitations & Future Work

### 9.1 Current Limitations
1. **PubMed Focus**: Google Scholar often blocked
2. **Language Bias**: English papers prioritized
3. **Date Range**: No temporal filtering
4. **Full-text**: Only title/abstract searched

### 9.2 Planned Enhancements
1. **Phase 3**: PDF download and LLM-based extraction
2. **US FDA**: Expand to US digital therapeutics
3. **EU Countries**: France, UK digital health directories
4. **Automated Updates**: Scheduled re-scraping

---

*Document Version: 1.0*  
*Last Updated: January 2026*  
*Author: Xhoel Bano*
