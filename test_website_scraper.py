#!/usr/bin/env python3
"""Standalone CLI to test website evidence scraping (browser-use + OpenAI).

Does not modify main.py or the orchestrator. Usage:

  python test_website_scraper.py --dtx "deprexis" --country germany
  python test_website_scraper.py --all --country germany
  python test_website_scraper.py --dtx "Some Name" --country usa --force
  python test_website_scraper.py --dtx "HelloBetter Diabetes" --country germany --force-website

Requires: OPENAI_API_KEY, OPENAI_MODEL in .env. Uses company_website from DTx data.
--force overrides the zero verified-registry-evidence rule; --force-website re-runs the browser if candidates/website already exists.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.data_manager import DataManager

from scrapers.evidence.website_scraper import (
    WebsiteEvidenceScraper,
    count_registry_verified_studies,
)


def _normalize_quotes(s: str) -> str:
    return (
        s.replace("'", "'")
        .replace("'", "'")
        .replace('"', '"')
        .replace('"', '"')
    )


def _load_dtx_list(country: str):
    dm = DataManager()
    if country.lower() == "germany":
        data = dm.load_dtx_data()
        cname = "Germany"
    elif country.lower() == "usa":
        data = dm.load_usa_dtx_data()
        cname = "USA"
    else:
        raise ValueError("country must be germany or usa")
    return cname, data.get("dtx_list", [])


def _filter_by_dtx_name(dtx_list, needle: str):
    n = _normalize_quotes(needle.lower())
    out = []
    for d in dtx_list:
        name = _normalize_quotes((d.get("dtx_name") or "").lower())
        if n in name:
            out.append(d)
    return out


async def _run_one(
    scraper: WebsiteEvidenceScraper,
    dtx: dict,
    country: str,
    max_steps: int,
    delay: float,
    *,
    skip_zero_check: bool,
    force_website: bool,
) -> None:
    rct, rwe = count_registry_verified_studies(scraper.evidence_dir, country, dtx.get("dtx_name", ""))
    total = rct + rwe
    name = dtx.get("dtx_name", "?")
    print(f"\n=== {name} ===")
    print(
        f"Registry verified evidence (pubmed/clinicaltrials/drks/isrctn only; website excluded): "
        f"RCT={rct}, RWE={rwe}, total={total}"
    )
    if not skip_zero_check and total > 0:
        print(
            "Skip: this DTx already has verified registry evidence. "
            "Website fallback is only for products with zero verified registry RCT/RWE. "
            "Pass --force to run the website scraper anyway."
        )
        return
    cw = (dtx.get("company_website") or "").strip()
    if not cw:
        print("Skip: no company_website in DTx record.")
        return
    print(f"company_website: {cw}")
    result = await scraper.scrape_dtx_website(
        dtx,
        country,
        max_agent_steps=max_steps,
        delay_seconds_after=delay,
        force=force_website,
    )
    if result.get("error"):
        print(f"Error: {result['error']}")
    print(
        f"Done: RCT={result.get('rct_count')}, RWE={result.get('rwe_count')}, "
        f"rejected={result.get('rejected_count')}, saved={result.get('saved')}"
    )
    if result.get("skipped_browser"):
        print(
            "Note: Re-used existing candidates/website/studies.json (no browser). "
            "Use --force-website to run the agent again."
        )
    prev = result.get("raw_agent_text_preview") or ""
    if prev:
        print(f"Agent output preview (first 500 chars):\n{prev[:500]}")


async def main_async(args: argparse.Namespace) -> None:
    country_name, dtx_list = _load_dtx_list(args.country)
    scraper = WebsiteEvidenceScraper(evidence_dir=args.evidence_dir)

    if args.dtx:
        print(
            "Note: By default we only run when verified registry RCT+RWE count is 0 "
            "(website source is not counted). Use --force to override."
        )
        matches = _filter_by_dtx_name(dtx_list, args.dtx)
        if not matches:
            print(f"No DTx matching '{args.dtx}' in {country_name}")
            return
        if len(matches) > 1:
            print(f"Warning: {len(matches)} matches; using the first.")
        dtx = matches[0]
        await _run_one(
            scraper,
            dtx,
            country_name,
            args.max_steps,
            args.delay,
            skip_zero_check=args.force,
            force_website=args.force_website,
        )
        return

    if args.all:
        delay = args.delay
        ran = 0
        for dtx in dtx_list:
            rct, rwe = count_registry_verified_studies(
                scraper.evidence_dir, country_name, dtx.get("dtx_name", "")
            )
            if rct + rwe > 0 and not args.force:
                continue
            if not (dtx.get("company_website") or "").strip():
                continue
            await _run_one(
                scraper,
                dtx,
                country_name,
                args.max_steps,
                delay,
                skip_zero_check=args.force,
                force_website=args.force_website,
            )
            ran += 1
            if args.limit and ran >= args.limit:
                print(f"Stopped after --limit={args.limit} runs.")
                break
        return

    print("Specify --dtx NAME or --all (see --help).")
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(description="Test website evidence scraper (browser-use)")
    p.add_argument(
        "--country",
        choices=["germany", "usa"],
        required=True,
        help="Which DTx dataset to use",
    )
    p.add_argument("--dtx", type=str, help="Substring match against dtx_name (one product)")
    p.add_argument(
        "--all",
        action="store_true",
        help="Run for every DTx with 0 registry verified evidence and a company_website",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Run even when verified registry evidence already exists (ignores zero-evidence rule)",
    )
    p.add_argument(
        "--force-website",
        action="store_true",
        dest="force_website",
        help="Re-run the browser agent even if candidates/website/studies.json already exists",
    )
    p.add_argument(
        "--evidence-dir",
        default="evidence",
        help="Evidence root folder (default: evidence)",
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=35,
        help="browser-use Agent max steps (default: 35)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=7.0,
        help="Seconds to sleep after each DTx run when using --all (default: 7)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="With --all, stop after N successful runs (0 = no limit)",
    )
    args = p.parse_args()
    if not args.dtx and not args.all:
        p.error("Provide --dtx or --all")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
