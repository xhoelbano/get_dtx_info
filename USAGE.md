# Using the pipeline (`run.py`)

`run.py` is a thin, input-driven orchestrator on top of the existing 3-phase
pipeline. You describe **one job** in a YAML file, run a single command, and get
a self-contained result folder with the analysis output and the LLM cost for
that run. The orchestrator only *calls* the existing phases - it does not change
any scraping/analysis behaviour.

```
Phase 1  scrape / research DTx  ->  Phase 2  find + classify evidence  ->  Phase 3  analyse evidence
```

## Quickstart

1. Edit [`Input/job.yaml`](Input/job.yaml) (or copy one of the
   [`Input/templates/`](Input/templates) files).
2. Run it:

```bash
python run.py                              # uses Input/job.yaml
python run.py --job Input/templates/4_dtx_name.yaml
python run.py --dry-run                    # print the plan, spend nothing
```

3. Find everything under `Output/<run-id>/` (see [Outputs](#outputs)).

> Tip: always do a `--dry-run` first. It validates the job, creates the run
> folder, and prints the exact phase steps without making any LLM calls.

## The four input modes

Set `mode:` in the job file to one of the following. Every mode also accepts the
shared options `limit` (int or null) and `sources`
(`all|pubmed|clinicaltrials|drks|isrctn`). Phase 2's Browser-Use website pass is
controlled by `ENABLE_WEBSITE_SEARCH` in `.env` (see
[Web search & providers](#web-search--providers)), not by the job file.

### 1. `diga_full` - the whole German DiGA directory

Scrapes every DTx from <https://diga.bfarm.de/>, then runs evidence + analysis
for all of them. This is a large run; use `limit` for a smaller test.

The directory scrape includes **delisted** ("Gestrichen") DiGA as well as
permanently and provisionally listed ones, so `company_name`/`dtx_name` searches
also match DiGA that have been removed from the directory (e.g. Vision2B / mebix).

```yaml
mode: diga_full
limit: null          # e.g. 5 to only process the first 5 DiGA in phases 2-3
sources: all
```

### 2. `company_csv` - a CSV of companies (general / non-German path)

LLM-researches every company in a CSV (with web search), then runs evidence +
analysis for the discovered DTx. The CSV must match the column layout of
[`Input/companies/example_company.csv`](Input/companies/example_company.csv)
(a copy of `data-format/us_company.csv`). Column names are mapped by the config
file's `csv_column_mappings`. Needs a web-search-capable provider (see
[Web search & providers](#web-search--providers)).

```yaml
mode: company_csv
csv_path: Input/companies/example_company.csv
config: config/usa.json   # general path uses the USA config (see "Other countries")
limit: null               # trims the CSV to the first N companies for testing
sources: all
```

### 3. `company_name` - one or more companies

`company` accepts a single name or a list of names.

```yaml
mode: company_name
company: "GAIA AG"                       # or: ["GAIA AG", "Newsenselab"]
has_diga: true                           # see below
```

- `has_diga: true` -> **German path.** The DiGA directory is searched and every
  DiGA whose manufacturer matches any of the given companies is scraped (a
  provider can have several products), then evidence + analysis run.
- `has_diga: false` -> **General path.** Each company's DTx are discovered by LLM
  web search (no CSV needed), then evidence + analysis run. Companies that yield
  no DTx are noted in `SUMMARY.md`; the run only stops if none of them do.

### 4. `dtx_name` - one or more DTx

`dtx` accepts a single name or a list of names.

```yaml
mode: dtx_name
dtx: "deprexis"                          # or: ["deprexis", "Vivira", "Kalmeda"]
is_diga: true                            # see below
```

- `is_diga: true` -> **German path.** The DiGA directory is searched for the
  matching product(s) (match the brand name as shown in the directory, e.g.
  `deprexis`, `Vivira`, `Kalmeda`), they are scraped, then evidence + analysis run.
- `is_diga: false` -> **General path.** Each DTx is looked up by LLM web search;
  those that exist have their details gathered, then evidence + analysis run.
  Names found for none of the inputs stop the run with a note in `SUMMARY.md`.

## Outputs

Each run creates a timestamped, self-contained folder:

```
Output/<YYYY-MM-DD_HHMM__mode__slug>/
  job.resolved.yaml   # exact inputs + provider/model + web-search state
  run.log             # full step-by-step log of every phase
  data/               # this run's DTx/company JSON (dtx_data_<country>.run.json)
                      #   + the CSV / scope snapshot used for the run
  evidence/           # this run's evidence trees, identical layout to evidence/:
                      #   <country>/<slug>/candidates|rejected|verified (raw files included)
  analysis/           # Phase 3 output: <model>/phase3_combined.json
                      #   + phase3_combined.csv (flat table) + by_dtx/
  metrics/
    costs.json        # per-phase + total tokens / USD / latency (machine readable)
    costs.md          # human-readable cost report
  SUMMARY.md          # what ran, counts, cost, where to find results
```

`Output/` is git-ignored. The run folder is a self-contained snapshot: the
DTx/company JSON for the run, the full evidence trees (raw, candidates, rejected,
verified) for the run's DTx, the Phase 3 analysis, and the cost report. The
pipeline's global working directories (`data/`, `evidence/`) are also kept.

## Cost & metrics

`run.py` records the start/end time of each phase, then slices the pipeline's
LLM metrics log (`data/llm_metrics.jsonl`) to that window and groups the calls
by phase and by call label. Tokens, estimated USD cost (from
`config/llm_pricing.json`), and latency are reported in `metrics/costs.md`.

**Known limitation:** the German Phase 1 *translation* step is not metered by
the pipeline, so its LLM cost does not appear in the report. USA research
(Phase 1, general path), Phase 2, and Phase 3 are fully metered.

## Other countries (general path)

The general (non-German) path uses `config/usa.json` by default - this is the
generic LLM-research + evidence path, not USA-specific data sources. To run it
for another country, add a `config/<country>.json` (same shape as
`config/usa.json`) and set `config:` in the job file. Output folders use the
general label regardless of country.

## Web search & providers

Provider and model selection is driven entirely by `.env` (`LLM_PROVIDER`,
`PHASE3_PROVIDER`/`PHASE3_MODEL`, `BROWSER_USE_PROVIDER`). The resolved values
are captured in each run's `job.resolved.yaml`.

There are two independent web toggles, both controlled in `.env` (the job file
no longer has a `website_search` option):

- `ENABLE_WEBSITE_SEARCH` - Phase 2's Browser-Use website agent (runs a real
  browser over each DTx's company website). Works with any provider.
- `ENABLE_WEB_SEARCH` - the native web-search tool used by the **general/US
  Phase 1 research** (modes `company_csv`, and `company_name`/`dtx_name` with
  `*_diga: false`).

**Important - the general/US path needs a web-search-capable provider.** The
native web-search tool is only available on `openai`, `gemini`, and `anthropic`.
**Azure OpenAI cannot web-search through this pipeline:** Azure only exposes web
search via its *Responses API* with admin-enabled "Grounding with Bing", which
this pipeline does not use (it uses Chat Completions). On `LLM_PROVIDER=azure_openai`
the general/US research falls back to a plain completion and will usually find no
DTx. `run.py` prints a warning and notes this in `SUMMARY.md` when it detects
this case. For general/US runs, set `LLM_PROVIDER=openai` (or `gemini`/`anthropic`).

The German/DiGA modes (`diga_full`, and `company_name`/`dtx_name` with
`*_diga: true`) are scraping-based and are **not** affected by this - they work
on any provider.
