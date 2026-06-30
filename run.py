#!/usr/bin/env python3
"""Input-driven orchestrator for the 3-phase DTx pipeline.

This is a thin, non-invasive wrapper around the existing pipeline. It reads a
single job file (``Input/job.yaml`` by default), figures out which of the four
run modes is requested, sequences the existing phases for that mode, and
collects the user-facing results plus per-run LLM cost/metrics into a
self-contained ``Output/<run-id>/`` folder.

It does NOT change any pipeline/scraper/analyzer behaviour:
- Phase 1 (German full) and Phase 2 are invoked through ``python main.py ...``.
- Phase 1 German *filtered* (single company / single DiGA) and Phase 1 USA
  *single company* reuse the public scraper classes directly so the run can be
  scoped to just the requested DTx.
- Phase 3 reuses ``Phase3Analyzer`` (public methods only), scoped to the run's
  DTx and written into the run folder.

Run modes (set ``mode:`` in the job file):
  1. diga_full     - scrape the whole DiGA directory, then evidence + analysis.
  2. company_csv   - research a CSV of companies (general path), then ev. + an.
  3. company_name  - one company; has_diga true=German path, false=general path.
  4. dtx_name      - one DTx; is_diga true=German path, false=general path.

Usage:
  python run.py                                  # uses Input/job.yaml
  python run.py --job Input/templates/4_dtx_name.yaml
  python run.py --dry-run                        # print the plan, spend nothing
"""
import argparse
import asyncio
import csv
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv
from slugify import slugify

# Mirror how the app loads .env so provider/model resolution matches the
# subprocesses and direct scraper calls.
load_dotenv(override=True)

ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "Output"
METRICS_LOG = ROOT / "data" / "llm_metrics.jsonl"

VALID_MODES = {"diga_full", "company_csv", "company_name", "dtx_name"}

# call_label prefixes -> the phase they belong to (for the cost breakdown).
LABEL_PHASE = {
    "usa_research": "Phase 1 (research)",
    "phase2_": "Phase 2 (evidence)",
    "phase3_": "Phase 3 (analysis)",
}


# =====================================================================
# Small helpers
# =====================================================================

def _slug(name: str) -> str:
    """Slug identical to the evidence folder + Phase 3 slug rules."""
    return slugify(name or "", max_length=50, lowercase=True)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", ""))


class RunLogger:
    """Tee log lines to the console and to ``run.log``."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self._fh = open(log_path, "a", encoding="utf-8")

    def __call__(self, msg: str = "") -> None:
        print(msg)
        self._fh.write(msg + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# =====================================================================
# Job loading / validation
# =====================================================================

def load_job(job_path: Path) -> Dict:
    if not job_path.exists():
        raise FileNotFoundError(f"Job file not found: {job_path}")
    with open(job_path, "r", encoding="utf-8") as fh:
        job = yaml.safe_load(fh) or {}
    if not isinstance(job, dict):
        raise ValueError(f"Job file {job_path} must be a YAML mapping.")

    mode = job.get("mode")
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid or missing 'mode': {mode!r}. "
            f"Choose one of {sorted(VALID_MODES)}."
        )

    # Per-mode required params. company/dtx may be a single string or a list;
    # both are normalised to a clean list of names.
    if mode == "company_csv" and not job.get("csv_path"):
        raise ValueError("mode 'company_csv' requires 'csv_path'.")
    if mode == "company_name":
        if "has_diga" not in job:
            raise ValueError("mode 'company_name' requires 'has_diga' (true/false).")
        job["company"] = _as_name_list(job.get("company"))
        if not job["company"]:
            raise ValueError("mode 'company_name' requires 'company' (name or list of names).")
    if mode == "dtx_name":
        if "is_diga" not in job:
            raise ValueError("mode 'dtx_name' requires 'is_diga' (true/false).")
        job["dtx"] = _as_name_list(job.get("dtx"))
        if not job["dtx"]:
            raise ValueError("mode 'dtx_name' requires 'dtx' (name or list of names).")

    # Normalise shared options with defaults. (Phase 2's browser-use pass is
    # controlled solely by ENABLE_WEBSITE_SEARCH in .env, not by the job file.)
    job.setdefault("sources", "all")
    job.setdefault("limit", None)
    return job


def _as_name_list(value) -> List[str]:
    """Normalize a company/dtx field (string or list) into a list of names."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [str(value)]
    return [str(x).strip() for x in items if str(x).strip()]


def _run_slug(job: Dict) -> str:
    """Short identifier for the run folder name."""
    mode = job["mode"]
    if mode == "diga_full":
        return "all-diga"
    if mode == "company_csv":
        return _slug(Path(job["csv_path"]).stem) or "csv"
    if mode == "company_name":
        return _multi_slug(job["company"], "company")
    if mode == "dtx_name":
        return _multi_slug(job["dtx"], "dtx")
    return "run"


def _multi_slug(names: List[str], fallback: str) -> str:
    """Run-folder slug for one or many names: first name, plus '+N' for extras."""
    names = names if isinstance(names, list) else [names]
    if not names:
        return fallback
    first = _slug(names[0]) or fallback
    return f"{first}+{len(names) - 1}" if len(names) > 1 else first


# =====================================================================
# Phase timing (for cost windowing)
# =====================================================================

@contextmanager
def phase(name: str, phases: List[Dict], log: RunLogger, dry_run: bool):
    start = datetime.utcnow()
    log(f"\n{'='*64}\n=== PHASE: {name}  (start {start.isoformat()}Z)\n{'='*64}")
    rec = {"phase": name, "start": start.isoformat() + "Z", "end": None}
    phases.append(rec)
    try:
        yield rec
    finally:
        end = datetime.utcnow()
        rec["end"] = end.isoformat() + "Z"
        dur = (end - start).total_seconds()
        log(f"--- PHASE done: {name}  ({dur:.1f}s){' [dry-run]' if dry_run else ''}")


# =====================================================================
# Subprocess phase runners (existing CLI commands, unchanged)
# =====================================================================

def _build_env() -> Dict[str, str]:
    """Environment for subprocesses.

    Inherits the current environment (including the ``.env`` already loaded by
    ``load_dotenv``), so Phase 2's browser-use website pass is controlled solely
    by ``ENABLE_WEBSITE_SEARCH`` in ``.env`` - not by the job file.
    """
    return dict(os.environ)


def run_cmd(args: List[str], env: Dict[str, str], log: RunLogger,
            dry_run: bool) -> int:
    """Run a subprocess, streaming combined output into the run log."""
    printable = " ".join(args)
    log(f"$ {printable}")
    if dry_run:
        log("  [dry-run] command not executed")
        return 0
    proc = subprocess.Popen(
        args, cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    proc.wait()
    log(f"  (exit code {proc.returncode})")
    return proc.returncode


def cmd_scrape_dtx_full(env: Dict[str, str], log: RunLogger, dry_run: bool) -> int:
    return run_cmd(
        [sys.executable, "main.py", "scrape-dtx",
         "--mode", "full", "--config", "config/germany.json"],
        env, log, dry_run,
    )


def cmd_scrape_usa(csv_path: str, config: str, env: Dict[str, str],
                   log: RunLogger, dry_run: bool) -> int:
    return run_cmd(
        [sys.executable, "main.py", "scrape-usa",
         "--csv", csv_path, "--config", config, "--merge"],
        env, log, dry_run,
    )


def cmd_find_evidence(country: str, env: Dict[str, str], log: RunLogger,
                      dry_run: bool, *, all_flag: bool = False,
                      dtx_file: Optional[str] = None,
                      sources: str = "all") -> int:
    args = [sys.executable, "main.py", "find-evidence",
            "--country", country, "--source", sources]
    if all_flag:
        args.append("--all")
    if dtx_file:
        args += ["--dtx-file", dtx_file]
    return run_cmd(args, env, log, dry_run)


# =====================================================================
# Direct scraper helpers (scoped Phase 1 paths)
# =====================================================================

async def german_filtered_scrape(predicate, log: RunLogger) -> List[Dict]:
    """Scrape only the DiGA matching ``predicate`` (list -> details -> translate).

    Returns the detailed, translated DTx entries (same shape as a full scrape's
    ``dtx_list``). Reuses ``DiGAScraper`` public methods only.
    """
    from scrapers import DiGAScraper
    from utils import Translator

    scraper = DiGAScraper(config_path="config/germany.json")
    scraper.translator = Translator(source_lang="de", target_lang="en")
    detailed: List[Dict] = []
    try:
        basic = await scraper.scrape_list_only()
        matched = [d for d in basic if predicate(d)]
        log(f"  Matched {len(matched)} of {len(basic)} DiGA entries.")
        for i, entry in enumerate(matched, 1):
            log(f"  [{i}/{len(matched)}] details: {entry.get('dtx_name')}")
            det = await scraper.scrape_dtx_details(entry)
            det = await scraper._translate_dtx_fields(det)
            detailed.append(det)
            await asyncio.sleep(1)
    finally:
        await scraper.close()
    return detailed


async def usa_single_company_scrape(target: str, log: RunLogger) -> Dict:
    """Research a single company/DTx via the USA LLM scraper (general path).

    Returns the scraper result dict (``metadata`` + ``dtx_list``). Reuses
    ``USAScraper`` public methods only.
    """
    from scrapers import USAScraper

    scraper = USAScraper(config_path="config/usa.json")
    try:
        return await scraper.scrape_single_company(target)
    finally:
        await scraper.close()


def save_german_entries(entries: List[Dict], log: RunLogger) -> None:
    """Augment data/dtx_data.json incrementally (never clobbers the global file)."""
    from utils import DataManager

    dm = DataManager()
    dm.update_dtx(
        {"metadata": {"country": "Germany"}, "dtx_list": entries},
        mode="incremental",
    )
    log(f"  Merged {len(entries)} German DTx into {dm.dtx_file} (incremental).")


def save_usa_result(result: Dict, log: RunLogger) -> None:
    """Merge a USA scraper result into data/dtx_data_usa.json."""
    from scrapers import USAScraper

    scraper = USAScraper(config_path="config/usa.json")
    existing = scraper.load_existing_data()
    if existing.get("dtx_list"):
        result = scraper.merge_results(existing, result)
    scraper.save_results(result)
    log(f"  Saved/merged USA DTx into {scraper.output_file}.")


# =====================================================================
# Phase 3 (scoped) - reuse Phase3Analyzer public methods
# =====================================================================

async def run_phase3_scoped(run_dir: Path, countries: Optional[List[str]],
                            scope_slugs: Optional[set], limit: Optional[int],
                            log: RunLogger) -> Dict:
    """Run Phase 3 scoped to ``scope_slugs`` (per country) into the run folder.

    Returns a small summary dict (rows, dtx, metrics totals, output path).
    """
    from utils import DataManager
    from utils.llm_metrics import aggregate
    from scrapers.evidence.phase3_analyzer import Phase3Analyzer

    analysis_dir = run_dir / "analysis"
    dm = DataManager()
    analyzer = Phase3Analyzer(
        data_manager=dm,
        limit=None,  # scoping is done here, not by the analyzer's limit
        output_dir=str(analysis_dir),
    )

    records = analyzer.discover_studies(countries=countries)
    if scope_slugs is not None:
        records = [r for r in records if r["dtx_slug"] in scope_slugs]
    if limit:
        # Keep first N DTx (preserving discovery order).
        kept, allowed = [], []
        for r in records:
            key = (r["country"], r["dtx_slug"])
            if key not in allowed:
                if len(allowed) >= limit:
                    continue
                allowed.append(key)
            kept.append(r)
        records = kept

    if not records:
        log("  No verified studies in scope - Phase 3 produced no rows.")
        return {"rows": 0, "dtx": 0, "output": None, "metrics": aggregate([])}

    rows, audits = [], []
    total = len(records)
    for idx, record in enumerate(records, 1):
        sid = analyzer._study_id(record["study"]) or "?"
        log(f"  [{idx}/{total}] {record['country']} / {record['dtx_slug']} / "
            f"{record['evidence_type']} / {record['source']} / {sid}")
        try:
            row, audit = await analyzer.analyze_record(record)
            rows.append(row)
            audits.append(audit)
        except Exception as exc:  # noqa: BLE001 - mirror analyze_all's tolerance
            log(f"    ERROR: {exc}")
    analyzer.audits = audits
    out_dir = analyzer.save_outputs(rows)

    totals = aggregate(analyzer.metrics) if analyzer.metrics else aggregate([])
    return {
        "rows": len(rows),
        "dtx": len({(a.get("country"), a.get("dtx_slug")) for a in audits}),
        "output": str(out_dir),
        "metrics": totals,
    }


# =====================================================================
# Scope helpers
# =====================================================================

def write_scope_file(run_dir: Path, names: List[str]) -> Path:
    """Write the DTx name substrings used to scope Phase 2 (--dtx-file)."""
    path = run_dir / "scope_dtx.txt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# DTx names scoped for this run (Phase 2 --dtx-file)\n")
        for n in names:
            if n:
                fh.write(n + "\n")
    return path


def usa_entries_for_companies(company_names: set) -> Tuple[List[Dict], List[str], set]:
    """Return (entries, dtx_names, slugs) for USA DTx whose provider is in
    ``company_names`` (read from data/dtx_data_usa.json after the scrape)."""
    from utils import DataManager

    dm = DataManager()
    entries, names, slugs = [], [], set()
    for d in dm.load_usa_dtx_data().get("dtx_list", []):
        if d.get("company_provider") in company_names:
            nm = d.get("dtx_name", "")
            if nm:
                entries.append(d)
                names.append(nm)
                slugs.add(_slug(nm))
    return entries, names, slugs


# =====================================================================
# Run archiving (item 1: snapshot DTx/company JSON + evidence into the run)
# =====================================================================

def save_run_dtx_json(run_dir: Path, country: str, entries: List[Dict],
                      log: RunLogger) -> None:
    """Snapshot the run-scoped DTx/company entries into the run's data/ folder."""
    if not entries:
        return
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"dtx_data_{country.lower()}.run.json"
    payload = {
        "metadata": {
            "country": country,
            "total_count": len(entries),
            "snapshot": _now_iso(),
        },
        "dtx_list": entries,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    log(f"  Saved {len(entries)} {country} DTx entries to {path}")


def copy_evidence(run_dir: Path, country: str, scope_slugs: Optional[set],
                  log: RunLogger) -> None:
    """Copy the per-DTx evidence trees (raw/candidates/rejected/verified) for the
    run's DTx into Output/<run>/evidence/<country>/<slug>/.

    When ``scope_slugs`` is None (full diga_full), the whole evidence/<country>/
    directory is copied.
    """
    src_country = ROOT / "evidence" / country
    if not src_country.exists():
        log(f"  No evidence directory for {country} to copy.")
        return
    dst_country = run_dir / "evidence" / country
    if scope_slugs is None:
        slugs = [d.name for d in src_country.iterdir() if d.is_dir()]
    else:
        slugs = sorted(scope_slugs)
    copied = 0
    for slug in slugs:
        src = src_country / slug
        if not src.is_dir():
            continue
        dst = dst_country / slug
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        copied += 1
    log(f"  Copied evidence for {copied} {country} DTx into {dst_country}")


# =====================================================================
# Web-search capability warning (item 4: docs + warn, no provider change)
# =====================================================================

def warn_if_no_web_search(log: RunLogger, summary: Dict) -> None:
    """Warn when the active provider can't use web search on a general/US run."""
    try:
        from utils.llm_provider import LLMProvider
        active = LLMProvider.web_search_active()
        provider = LLMProvider.get_active_provider()
    except Exception:
        return
    if active:
        return
    msg = (
        f"Active provider '{provider}' cannot use web search (e.g. Azure OpenAI "
        f"via Chat Completions), so the general/US research will likely find no "
        f"DTx. Set LLM_PROVIDER to openai/gemini/anthropic for these runs."
    )
    log(f"  WARNING: {msg}")
    summary.setdefault("notes", []).append("WARNING: " + msg)


# =====================================================================
# Mode dispatch
# =====================================================================

async def dispatch(job: Dict, run_dir: Path, env: Dict[str, str],
                   phases: List[Dict], log: RunLogger, dry_run: bool) -> Dict:
    """Execute the phases for the requested mode. Returns a summary dict."""
    mode = job["mode"]
    limit = job.get("limit")
    sources = job.get("sources", "all")
    summary: Dict = {"mode": mode, "phase3": None, "notes": []}

    # -----------------------------------------------------------------
    # MODE 1: full German DiGA path
    # -----------------------------------------------------------------
    if mode == "diga_full":
        with phase("Phase 1 - scrape DiGA directory (full)", phases, log, dry_run):
            cmd_scrape_dtx_full(env, log, dry_run)

        scope_slugs = None
        dtx_file = None
        if not dry_run:
            from utils import DataManager
            g_list = DataManager().load_dtx_data().get("dtx_list", [])
            if limit:
                g_list = g_list[:limit]
                names = [d.get("dtx_name", "") for d in g_list if d.get("dtx_name")]
                scope_slugs = {_slug(n) for n in names}
                dtx_file = str(write_scope_file(run_dir, names))
                log(f"  limit={limit}: scoping evidence+analysis to {len(names)} DTx.")
            save_run_dtx_json(run_dir, "Germany", g_list, log)

        with phase("Phase 2 - find evidence (Germany)", phases, log, dry_run):
            cmd_find_evidence("germany", env, log, dry_run,
                              all_flag=dtx_file is None, dtx_file=dtx_file,
                              sources=sources)
        if not dry_run:
            copy_evidence(run_dir, "Germany", scope_slugs, log)

        with phase("Phase 3 - analyse evidence (Germany)", phases, log, dry_run):
            if not dry_run:
                summary["phase3"] = await run_phase3_scoped(
                    run_dir, ["Germany"], scope_slugs, limit, log)
            else:
                log("  [dry-run] would analyse verified Germany studies into "
                    f"{run_dir/'analysis'}")
        return summary

    # -----------------------------------------------------------------
    # MODE 2: company CSV (general path)
    # -----------------------------------------------------------------
    if mode == "company_csv":
        config = job.get("config", "config/usa.json")
        src_csv = Path(job["csv_path"])
        run_csv = run_dir / "data" / src_csv.name
        company_names: set = set()
        if not dry_run:
            if not src_csv.exists():
                raise FileNotFoundError(f"csv_path not found: {src_csv}")
            company_names = _copy_csv(src_csv, run_csv, limit)
            log(f"  Using CSV: {run_csv} ({len(company_names)} companies"
                f"{f', limited to {limit}' if limit else ''}).")
        used_csv = str(run_csv if not dry_run else src_csv)

        warn_if_no_web_search(log, summary)
        with phase("Phase 1 - research companies (general path)", phases, log, dry_run):
            cmd_scrape_usa(used_csv, config, env, log, dry_run)

        names, scope_slugs = ([], None)
        dtx_file = None
        if not dry_run:
            entries, names, scope_slugs = usa_entries_for_companies(company_names)
            dtx_file = str(write_scope_file(run_dir, names))
            save_run_dtx_json(run_dir, "USA", entries, log)
            log(f"  Discovered {len(names)} DTx for these companies.")

        with phase("Phase 2 - find evidence (general/USA)", phases, log, dry_run):
            cmd_find_evidence("usa", env, log, dry_run,
                              all_flag=False, dtx_file=dtx_file, sources=sources)
        if not dry_run:
            copy_evidence(run_dir, "USA", scope_slugs, log)

        with phase("Phase 3 - analyse evidence (general/USA)", phases, log, dry_run):
            if not dry_run:
                summary["phase3"] = await run_phase3_scoped(
                    run_dir, ["USA"], scope_slugs, None, log)
            else:
                log("  [dry-run] would analyse verified USA studies into "
                    f"{run_dir/'analysis'}")
        return summary

    # -----------------------------------------------------------------
    # MODE 3: company name (+/- DiGA)
    # MODE 4: dtx name (+/- DiGA)
    # -----------------------------------------------------------------
    is_german = (
        (mode == "company_name" and job.get("has_diga")) or
        (mode == "dtx_name" and job.get("is_diga"))
    )

    targets = job.get("company") if mode == "company_name" else job.get("dtx")
    target_label = ", ".join(targets)

    if is_german:
        field = "company_provider" if mode == "company_name" else "dtx_name"
        needles = [t.lower() for t in targets]

        def predicate(entry: Dict) -> bool:
            hay = (entry.get(field, "") or "").lower()
            return any(n in hay for n in needles)

        entries: List[Dict] = []
        with phase(f"Phase 1 - scrape matching DiGA ({field} ~ {target_label})",
                   phases, log, dry_run):
            if not dry_run:
                entries = await german_filtered_scrape(predicate, log)
                if not entries:
                    summary["notes"].append(
                        f"No DiGA found where {field} matches any of {targets}. Stopped.")
                    log(f"  No DiGA matched {targets}. Stopping.")
                    return summary
                save_german_entries(entries, log)
                save_run_dtx_json(run_dir, "Germany", entries, log)
            else:
                log(f"  [dry-run] would scrape DiGA list, keep entries where "
                    f"{field} matches any of {targets}, fetch details, merge incrementally.")

        names = [e.get("dtx_name", "") for e in entries if e.get("dtx_name")]
        if limit:
            names = names[:limit]
        scope_slugs = {_slug(n) for n in names} if names else None
        dtx_file = str(write_scope_file(run_dir, names)) if names else None

        with phase("Phase 2 - find evidence (Germany)", phases, log, dry_run):
            cmd_find_evidence("germany", env, log, dry_run,
                              all_flag=False, dtx_file=dtx_file, sources=sources)
        if not dry_run:
            copy_evidence(run_dir, "Germany", scope_slugs, log)

        with phase("Phase 3 - analyse evidence (Germany)", phases, log, dry_run):
            if not dry_run:
                summary["phase3"] = await run_phase3_scoped(
                    run_dir, ["Germany"], scope_slugs, limit, log)
            else:
                log("  [dry-run] would analyse the matched DiGA's verified studies.")
        return summary

    # General path for modes 3/4 (no DiGA): LLM web-search research over each
    # requested name.
    produced: List[Dict] = []
    warn_if_no_web_search(log, summary)
    with phase(f"Phase 1 - research {target_label} (general/web-search path)",
               phases, log, dry_run):
        if not dry_run:
            for name in targets:
                result = await usa_single_company_scrape(name, log)
                n_products = len(result.get("dtx_list", []))
                if n_products == 0:
                    summary["notes"].append(f"No DTx found for '{name}' via web search.")
                    log(f"  No DTx found for '{name}'.")
                    continue
                save_usa_result(result, log)
                produced.extend(result.get("dtx_list", []))
                log(f"  Found {n_products} DTx for '{name}'.")
            if not produced:
                summary["notes"].append(
                    "No DTx found for any requested name. Stopped.")
                log("  No DTx found for any requested name. Stopping.")
                return summary
            save_run_dtx_json(run_dir, "USA", produced, log)
        else:
            log(f"  [dry-run] would web-search {targets}, save any DTx found, "
                "or stop if none found for any name.")

    names = [d.get("dtx_name", "") for d in produced if d.get("dtx_name")]
    if limit:
        names = names[:limit]
    scope_slugs = {_slug(n) for n in names} if names else None
    dtx_file = str(write_scope_file(run_dir, names)) if names else None

    with phase("Phase 2 - find evidence (general/USA)", phases, log, dry_run):
        cmd_find_evidence("usa", env, log, dry_run,
                          all_flag=False, dtx_file=dtx_file, sources=sources)
    if not dry_run:
        copy_evidence(run_dir, "USA", scope_slugs, log)

    with phase("Phase 3 - analyse evidence (general/USA)", phases, log, dry_run):
        if not dry_run:
            summary["phase3"] = await run_phase3_scoped(
                run_dir, ["USA"], scope_slugs, limit, log)
        else:
            log("  [dry-run] would analyse the discovered DTx's verified studies.")
    return summary


def _copy_csv(src: Path, dst: Path, limit: Optional[int]) -> set:
    """Copy the input CSV into the run folder (optionally trimmed to ``limit``
    data rows) and return the set of company names it contains."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "r", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))
    if limit:
        reader = reader[:limit]
    # Determine the company-name column from common header variants.
    name_col = None
    if reader:
        for cand in ("CompanyName", "Company Name", "company_name"):
            if cand in reader[0]:
                name_col = cand
                break
    fieldnames = list(reader[0].keys()) if reader else []
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(reader)
    return {r[name_col] for r in reader if name_col and r.get(name_col)}


# =====================================================================
# Cost / metrics aggregation
# =====================================================================

def _label_phase(label: str) -> str:
    for prefix, ph in LABEL_PHASE.items():
        if (label or "").startswith(prefix):
            return ph
    return "Other"


def collect_costs(phases: List[Dict]) -> Dict:
    """Slice data/llm_metrics.jsonl to this run's window and group by phase/label.

    Phase 1 (German translation) is not metered by the pipeline today, so it
    will not appear here; everything else (USA research, Phase 2, Phase 3) is.
    """
    from utils.llm_metrics import aggregate

    if not phases:
        return {"window": None, "by_phase": {}, "by_label": {}, "total": aggregate([])}

    starts = [_parse_iso(p["start"]) for p in phases if p.get("start")]
    ends = [_parse_iso(p["end"]) for p in phases if p.get("end")]
    win_start = min(starts)
    win_end = max(ends) if ends else datetime.utcnow()

    rows: List[Dict] = []
    if METRICS_LOG.exists():
        for line in METRICS_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = m.get("timestamp")
            if not ts:
                continue
            try:
                t = _parse_iso(ts)
            except ValueError:
                continue
            if win_start <= t <= win_end:
                rows.append(m)

    by_phase: Dict[str, List[Dict]] = {}
    by_label: Dict[str, List[Dict]] = {}
    for m in rows:
        by_phase.setdefault(_label_phase(m.get("call_label", "")), []).append(m)
        by_label.setdefault(m.get("call_label", "") or "(none)", []).append(m)

    return {
        "window": {"start": win_start.isoformat() + "Z",
                   "end": win_end.isoformat() + "Z"},
        "by_phase": {k: aggregate(v) for k, v in by_phase.items()},
        "by_label": {k: aggregate(v) for k, v in by_label.items()},
        "total": aggregate(rows),
        "calls_captured": len(rows),
    }


def _fmt_usd(v) -> str:
    return f"${v:.4f}" if isinstance(v, (int, float)) else "n/a"


def write_costs(run_dir: Path, costs: Dict, phases: List[Dict]) -> None:
    metrics_dir = run_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    with open(metrics_dir / "costs.json", "w", encoding="utf-8") as fh:
        json.dump({"phases": phases, "costs": costs}, fh, indent=2, ensure_ascii=False)

    total = costs.get("total", {})
    lines = [
        "# Per-run LLM cost & metrics",
        "",
        f"- Calls captured: **{costs.get('calls_captured', 0)}**",
        f"- Total tokens: **{total.get('total_tokens', 0)}**",
        f"- Estimated cost: **{_fmt_usd(total.get('total_estimated_cost_usd'))}**",
        f"- Total LLM latency: **{total.get('total_latency_ms', 0)} ms**",
        "",
        "> Note: German Phase 1 translation is not metered by the pipeline, so",
        "> its LLM cost is not captured here. USA research, Phase 2, and Phase 3",
        "> are fully metered.",
        "",
        "## By phase",
        "",
        "| Phase | Calls | Tokens | Est. cost (USD) | Latency (ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    for ph, agg in costs.get("by_phase", {}).items():
        lines.append(
            f"| {ph} | {agg.get('total_calls', 0)} | {agg.get('total_tokens', 0)} | "
            f"{_fmt_usd(agg.get('total_estimated_cost_usd'))} | "
            f"{agg.get('total_latency_ms', 0)} |"
        )
    lines += [
        "",
        "## By call label",
        "",
        "| Label | Calls | Tokens | Est. cost (USD) |",
        "|---|---:|---:|---:|",
    ]
    for lbl, agg in costs.get("by_label", {}).items():
        lines.append(
            f"| {lbl} | {agg.get('total_calls', 0)} | {agg.get('total_tokens', 0)} | "
            f"{_fmt_usd(agg.get('total_estimated_cost_usd'))} |"
        )
    with open(metrics_dir / "costs.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# =====================================================================
# Summary
# =====================================================================

def write_summary(run_dir: Path, job: Dict, summary: Dict, costs: Dict,
                  phases: List[Dict], dry_run: bool) -> None:
    total = costs.get("total", {})
    p3 = summary.get("phase3") or {}
    lines = [
        f"# Run summary - {run_dir.name}",
        "",
        f"- Mode: **{job['mode']}**",
        f"- Dry run: **{dry_run}**",
        f"- Website search (Phase 2, .env ENABLE_WEBSITE_SEARCH): "
        f"**{os.getenv('ENABLE_WEBSITE_SEARCH', '(unset)')}**",
        f"- Sources: **{job.get('sources')}**",
        f"- Limit: **{job.get('limit')}**",
        "",
        "## Phases",
        "",
        "| Phase | Start | End |",
        "|---|---|---|",
    ]
    for p in phases:
        lines.append(f"| {p['phase']} | {p.get('start','')} | {p.get('end','')} |")

    lines += ["", "## Results", ""]
    if summary.get("notes"):
        for n in summary["notes"]:
            lines.append(f"- {n}")
    if p3:
        lines += [
            f"- Phase 3 rows (studies analysed): **{p3.get('rows', 0)}**",
            f"- Phase 3 DTx covered: **{p3.get('dtx', 0)}**",
            f"- Phase 3 output: `{p3.get('output')}`",
        ]
    if not summary.get("notes") and not p3:
        lines.append("- (No Phase 3 results - see run.log / dry-run.)")

    lines += [
        "",
        "## Cost",
        "",
        f"- Calls captured: **{costs.get('calls_captured', 0)}**",
        f"- Total tokens: **{total.get('total_tokens', 0)}**",
        f"- Estimated cost: **{_fmt_usd(total.get('total_estimated_cost_usd'))}**",
        "- Details: [`metrics/costs.md`](metrics/costs.md)",
        "",
        "## Where to find things",
        "",
        "- `job.resolved.yaml` - the exact inputs + provider/model used",
        "- `run.log` - full step-by-step log",
        "- `data/` - the run's DTx/company JSON (`dtx_data_<country>.run.json`) "
        "+ CSV / scope snapshot",
        "- `evidence/` - this run's evidence trees "
        "(`<country>/<slug>/candidates|rejected|verified`, raw files included)",
        "- `analysis/` - Phase 3 output "
        "(`<model>/phase3_combined.json` + `phase3_combined.csv`)",
        "- `metrics/` - cost JSON + report",
        "",
        "The global working directories (`data/`, `evidence/`) the pipeline writes",
        "to are also kept; this folder is the self-contained snapshot for the run.",
    ]
    with open(run_dir / "SUMMARY.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _web_search_active_safe() -> Optional[bool]:
    """Provider web-search capability for the active LLM_PROVIDER (or None)."""
    try:
        from utils.llm_provider import LLMProvider
        return LLMProvider.web_search_active()
    except Exception:
        return None


def write_resolved_job(run_dir: Path, job: Dict) -> None:
    resolved = dict(job)
    resolved["_resolved"] = {
        "run_id": run_dir.name,
        "timestamp": _now_iso(),
        "llm_provider": os.getenv("LLM_PROVIDER"),
        "phase3_provider": os.getenv("PHASE3_PROVIDER") or os.getenv("LLM_PROVIDER"),
        "phase3_model": os.getenv("PHASE3_MODEL"),
        "browser_use_provider": os.getenv("BROWSER_USE_PROVIDER") or os.getenv("LLM_PROVIDER"),
        # Phase 2 browser-use website pass (controlled by .env, not the job file).
        "enable_website_search": os.getenv("ENABLE_WEBSITE_SEARCH"),
        # Phase 1 native web search (general/US research path).
        "enable_web_search": os.getenv("ENABLE_WEB_SEARCH"),
        "web_search_active": _web_search_active_safe(),
    }
    with open(run_dir / "job.resolved.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(resolved, fh, sort_keys=False, allow_unicode=True)


# =====================================================================
# Entry point
# =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", default="Input/job.yaml",
                        help="Path to the job YAML (default: Input/job.yaml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the planned phases without spending LLM calls")
    args = parser.parse_args()

    job_path = (ROOT / args.job) if not os.path.isabs(args.job) else Path(args.job)
    job = load_job(job_path)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_id = f"{stamp}__{job['mode']}__{_run_slug(job)}"
    run_dir = OUTPUT_ROOT / run_id
    for sub in ("", "data", "analysis", "metrics"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    log = RunLogger(run_dir / "run.log")
    phases: List[Dict] = []
    try:
        log(f"Run id: {run_id}")
        log(f"Job: {job_path}")
        log(f"Mode: {job['mode']} | dry_run={args.dry_run}")
        write_resolved_job(run_dir, job)
        env = _build_env()

        summary = asyncio.run(
            dispatch(job, run_dir, env, phases, log, args.dry_run))

        costs = collect_costs(phases)
        write_costs(run_dir, costs, phases)
        write_summary(run_dir, job, summary, costs, phases, args.dry_run)

        log(f"\nDone. Output: {run_dir}")
        log(f"  Summary:  {run_dir/'SUMMARY.md'}")
        log(f"  Costs:    {run_dir/'metrics'/'costs.md'}")
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
