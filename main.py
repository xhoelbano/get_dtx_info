#!/usr/bin/env python3
"""CLI entry point for DTx data scraping system."""
import asyncio
import click
from pathlib import Path

from scrapers import DiGAScraper, USAScraper
from utils import DataManager, Translator


@click.group()
def cli():
    """DTx Data Scraping System - Extract Digital Therapeutics information."""
    pass


@cli.command()
@click.option('--mode', type=click.Choice(['full', 'incremental']), default='full',
              help='Scraping mode: full refresh or incremental update')
@click.option('--config', type=click.Path(exists=True), default='config/germany.json',
              help='Path to country configuration file')
@click.option('--list-only', is_flag=True, help='Only scrape the list, not details')
@click.option('--skip-details', is_flag=True, help='Scrape list but skip individual detail pages')
@click.option('--no-translate', is_flag=True, help='Skip translation to English')
def scrape_dtx(mode: str, config: str, list_only: bool, skip_details: bool, no_translate: bool):
    """Scrape DTx data from the DiGA directory."""
    click.echo(f"Starting DTx scrape (mode: {mode}, config: {config})")
    
    async def run():
        scraper = DiGAScraper(config_path=config)
        data_manager = DataManager()
        
        # Use local variable to allow modification
        scrape_mode = mode
        
        try:
            if list_only:
                click.echo("Scraping DTx list only...")
                dtx_list = await scraper.scrape_list_only()
                data = {
                    "metadata": {"country": "Germany"},
                    "dtx_list": dtx_list
                }
            else:
                translate = not no_translate
                click.echo(f"Scraping DTx data (mode: {scrape_mode}, translate: {translate}, skip_details: {skip_details})...")
                
                # Load existing data for incremental mode
                existing_data = None
                if scrape_mode == "incremental":
                    existing_data = data_manager.load_dtx_data()
                    if existing_data.get("dtx_list"):
                        click.echo(f"Loaded {len(existing_data['dtx_list'])} existing DTx entries")
                    else:
                        click.echo("No existing data found, switching to full mode")
                        scrape_mode = "full"
                
                data = await scraper.scrape(
                    mode=scrape_mode, 
                    translate=translate,
                    existing_data=existing_data,
                    skip_details=skip_details
                )
            
            # Save data
            updated_data = data_manager.update_dtx(data, mode=scrape_mode)
            
            click.echo(f"\nScraping complete!")
            click.echo(f"Total DTx: {updated_data['metadata']['total_count']}")
            click.echo(f"Active: {updated_data['metadata']['active_count']}")
            click.echo(f"Provisional: {updated_data['metadata']['provisional_count']}")
            click.echo(f"Delisted: {updated_data['metadata']['delisted_count']}")
            click.echo(f"Data saved to: {data_manager.dtx_file}")
            
        finally:
            await scraper.close()
    
    asyncio.run(run())


@cli.command()
@click.option('--config', type=click.Path(exists=True), default='config/germany.json',
              help='Path to country configuration file')
def scrape_reviews(config: str):
    """Scrape app store reviews and ratings for all DTx with store URLs."""
    click.echo("Starting app store review scraping...")
    
    async def run():
        # Import here to avoid circular imports
        from scrapers.app_store_scraper import AppStoreScraper
        
        data_manager = DataManager()
        scraper = AppStoreScraper(config_path=config)
        
        # Load existing DTx data
        dtx_data = data_manager.load_dtx_data()
        dtx_list = dtx_data.get("dtx_list", [])
        
        # Count DTx with store URLs
        with_play = sum(1 for d in dtx_list if d.get("play_store_url"))
        with_app = sum(1 for d in dtx_list if d.get("app_store_url"))
        click.echo(f"Found {with_play} DTx with Play Store URLs, {with_app} with App Store URLs")
        
        play_success = 0
        app_success = 0
        
        try:
            total = len(dtx_list)
            for i, dtx in enumerate(dtx_list, 1):
                dtx_name = dtx.get("dtx_name", "Unknown")
                play_store_url = dtx.get("play_store_url")
                app_store_url = dtx.get("app_store_url")
                
                # Skip if no store URLs
                if not play_store_url and not app_store_url:
                    continue
                
                click.echo(f"[{i}/{total}] {dtx_name[:50]}...")
                
                if play_store_url:
                    reviews = await scraper.scrape_play_store(play_store_url)
                    if reviews and reviews.get("rating"):
                        dtx["reviews_playstore"] = {
                            "rating": reviews.get("rating"),
                            "review_count": reviews.get("review_count"),
                            "url": play_store_url
                        }
                        click.echo(f"    Play Store: {reviews.get('rating')} ★ ({reviews.get('review_count')} reviews)")
                        play_success += 1
                    else:
                        dtx["reviews_playstore"] = None
                
                if app_store_url:
                    reviews = await scraper.scrape_app_store(app_store_url)
                    if reviews and reviews.get("rating"):
                        dtx["reviews_appstore"] = {
                            "rating": reviews.get("rating"),
                            "review_count": reviews.get("review_count"),
                            "url": app_store_url
                        }
                        click.echo(f"    App Store: {reviews.get('rating')} ★ ({reviews.get('review_count')} reviews)")
                        app_success += 1
                    else:
                        dtx["reviews_appstore"] = None
                
                await asyncio.sleep(1)  # Rate limiting
            
            # Save updated data
            data_manager.save_dtx_data(dtx_data)
            
            click.echo(f"\nScraping complete!")
            click.echo(f"Play Store ratings extracted: {play_success}/{with_play}")
            click.echo(f"App Store ratings extracted: {app_success}/{with_app}")
            click.echo(f"Data saved to: {data_manager.dtx_file}")
            
        finally:
            await scraper.close()
    
    asyncio.run(run())


@cli.command()
@click.option('--all', 'scrape_all', is_flag=True, help='Find evidence for all DTx')
@click.option('--dtx', type=str, help='Find evidence for specific DTx by name')
@click.option('--skip-scholar', is_flag=True, help='Skip Google Scholar (use PubMed only)')
@click.option('--config', type=click.Path(exists=True), default='config/germany.json',
              help='Path to country configuration file')
def find_evidence(scrape_all: bool, dtx: str, skip_scholar: bool, config: str):
    """Find RCT/RWE evidence for DTx from PubMed and Google Scholar.
    
    Uses PubMed E-utilities API (free, reliable) and optionally
    browser-use for Google Scholar searches.
    
    Results are classified as RCT (Randomized Controlled Trial) or
    RWE (Real-World Evidence) based on publication type and keywords.
    """
    if not scrape_all and not dtx:
        click.echo("Error: Please specify --all or --dtx <name>")
        return
    
    click.echo("Starting evidence search...")
    click.echo("  - PubMed: Using E-utilities API")
    if not skip_scholar:
        click.echo("  - Google Scholar: Using browser-use (may be blocked)")
    
    async def run():
        from scrapers.evidence_scraper import EvidenceScraper
        
        data_manager = DataManager()
        scraper = EvidenceScraper(config_path=config)
        
        # Load existing DTx data
        dtx_data = data_manager.load_dtx_data()
        dtx_list = dtx_data.get("dtx_list", [])
        
        if dtx:
            # Filter to specific DTx
            dtx_list = [d for d in dtx_list if dtx.lower() in d.get("dtx_name", "").lower()]
            if not dtx_list:
                click.echo(f"No DTx found matching: {dtx}")
                return
        
        click.echo(f"\nSearching evidence for {len(dtx_list)} DTx...")
        
        total_rct = 0
        total_rwe = 0
        dtx_with_evidence = 0
        
        try:
            for i, dtx_item in enumerate(dtx_list, 1):
                dtx_name = dtx_item.get("dtx_name", "Unknown")
                click.echo(f"\n[{i}/{len(dtx_list)}] {dtx_name}")
                
                # Search for evidence
                evidence_result = await scraper.search_evidence(dtx_item)
                
                rct_count = len(evidence_result.get("RCT", []))
                rwe_count = len(evidence_result.get("RWE", []))
                
                if rct_count > 0 or rwe_count > 0:
                    # Save evidence for this DTx
                    data_manager.add_evidence_for_dtx(dtx_name, evidence_result)
                    dtx_with_evidence += 1
                    total_rct += rct_count
                    total_rwe += rwe_count
                    
                    # Show some results
                    for paper in evidence_result.get("RCT", [])[:2]:
                        click.echo(f"    [RCT] {paper.get('title', 'Unknown')[:55]}...")
                    for paper in evidence_result.get("RWE", [])[:2]:
                        click.echo(f"    [RWE] {paper.get('title', 'Unknown')[:55]}...")
                else:
                    click.echo("    No evidence found")
                
                await asyncio.sleep(1)  # Rate limiting
            
            click.echo(f"\n{'='*50}")
            click.echo("Evidence search complete!")
            click.echo(f"  DTx searched: {len(dtx_list)}")
            click.echo(f"  DTx with evidence: {dtx_with_evidence}")
            click.echo(f"  Total RCT papers: {total_rct}")
            click.echo(f"  Total RWE papers: {total_rwe}")
            click.echo(f"  Data saved to: {data_manager.evidence_file}")
            
        finally:
            await scraper.close()
    
    asyncio.run(run())


@cli.command()
@click.option('--csv', 'csv_path', type=click.Path(exists=True), 
              help='Path to CSV file with company data')
@click.option('--company', type=str, help='Filter to specific company name')
@click.option('--config', type=click.Path(exists=True), default='config/usa.json',
              help='Path to USA configuration file')
@click.option('--merge', 'merge_existing', is_flag=True, 
              help='Merge with existing USA DTx data instead of replacing')
def scrape_usa(csv_path: str, company: str, config: str, merge_existing: bool):
    """Scrape USA DTx data using LLM-based research.
    
    This command reads company data from a CSV file and uses Azure OpenAI
    to research and extract Digital Therapeutics information for each company.
    
    Examples:
        python main.py scrape-usa --csv data-format/us_company.csv
        python main.py scrape-usa --company "Pear Therapeutics"
        python main.py scrape-usa --csv companies.csv --merge
    """
    click.echo("Starting USA DTx research with LLM...")
    
    async def run():
        scraper = USAScraper(config_path=config)
        
        try:
            # If no CSV provided, use default from config
            input_csv = csv_path or scraper.csv_input_path
            
            if not Path(input_csv).exists():
                click.echo(f"Error: CSV file not found: {input_csv}")
                click.echo("Please provide a CSV file with --csv option or update config/usa.json")
                return
            
            click.echo(f"Input CSV: {input_csv}")
            if company:
                click.echo(f"Filtering to company: {company}")
            
            # Run the research
            data = await scraper.scrape(csv_path=input_csv, company_filter=company)
            
            # Handle merge mode
            if merge_existing:
                existing_data = scraper.load_existing_data()
                if existing_data.get("dtx_list"):
                    click.echo(f"Merging with {len(existing_data['dtx_list'])} existing entries...")
                    data = scraper.merge_results(existing_data, data)
            
            # Save results
            scraper.save_results(data)
            
            click.echo(f"\nUSA DTx research complete!")
            click.echo(f"Total DTx products: {data['metadata']['total_count']}")
            click.echo(f"Data saved to: {scraper.output_file}")
            
        finally:
            await scraper.close()
    
    asyncio.run(run())


@cli.command()
@click.argument('company_name')
@click.option('--website', type=str, help='Company website URL (optional)')
@click.option('--config', type=click.Path(exists=True), default='config/usa.json',
              help='Path to USA configuration file')
def research_company(company_name: str, website: str, config: str):
    """Research a single US company for DTx products using LLM.
    
    This is useful for quickly researching a specific company without
    needing a CSV file.
    
    Example:
        python main.py research-company "Pear Therapeutics" --website https://peartherapeutics.com
    """
    click.echo(f"Researching company: {company_name}")
    
    async def run():
        scraper = USAScraper(config_path=config)
        
        try:
            data = await scraper.scrape_single_company(company_name, website)
            
            if data.get("dtx_list"):
                click.echo(f"\nFound {len(data['dtx_list'])} DTx product(s):")
                for dtx in data["dtx_list"]:
                    click.echo(f"\n  Product: {dtx.get('dtx_name', 'Unknown')}")
                    click.echo(f"  Status: {dtx.get('listing_status', 'Unknown')}")
                    click.echo(f"  FDA Clearance: {dtx.get('fda_clearance', 'Unknown')}")
                    if dtx.get('clinical_area_icd10'):
                        click.echo(f"  ICD-10 Codes: {', '.join(dtx['clinical_area_icd10'])}")
                    if dtx.get('app_store_url'):
                        click.echo(f"  App Store: {dtx['app_store_url']}")
                    if dtx.get('play_store_url'):
                        click.echo(f"  Play Store: {dtx['play_store_url']}")
            else:
                click.echo("\nNo DTx products found for this company.")
            
        finally:
            await scraper.close()
    
    asyncio.run(run())


@cli.command()
def show_status():
    """Show current data status for Germany and USA DTx."""
    import json
    data_manager = DataManager()
    
    # === Germany DTx Status ===
    dtx_data = data_manager.load_dtx_data()
    metadata = dtx_data.get("metadata", {})
    
    click.echo("\n=== Germany DTx Data Status ===")
    click.echo(f"Country: {metadata.get('country', 'Germany')}")
    click.echo(f"Last updated: {metadata.get('last_updated', 'Never')}")
    click.echo(f"Total DTx: {metadata.get('total_count', 0)}")
    click.echo(f"  - Permanently listed: {metadata.get('active_count', 0)}")
    click.echo(f"  - Provisionally listed: {metadata.get('provisional_count', 0)}")
    click.echo(f"  - Delisted: {metadata.get('delisted_count', 0)}")
    
    # Count DTx with app store reviews
    dtx_list = dtx_data.get("dtx_list", [])
    with_playstore = sum(1 for d in dtx_list if d.get("reviews_playstore"))
    with_appstore = sum(1 for d in dtx_list if d.get("reviews_appstore"))
    click.echo(f"\nApp Store Reviews:")
    click.echo(f"  - Play Store ratings: {with_playstore}")
    click.echo(f"  - App Store ratings: {with_appstore}")
    
    # === USA DTx Status ===
    usa_data_path = Path("data/dtx_data_usa.json")
    if usa_data_path.exists():
        with open(usa_data_path, "r", encoding="utf-8") as f:
            usa_data = json.load(f)
        
        usa_metadata = usa_data.get("metadata", {})
        usa_dtx_list = usa_data.get("dtx_list", [])
        
        click.echo("\n=== USA DTx Data Status ===")
        click.echo(f"Country: {usa_metadata.get('country', 'USA')}")
        click.echo(f"Last updated: {usa_metadata.get('last_updated', 'Never')}")
        click.echo(f"Total DTx products: {usa_metadata.get('total_count', 0)}")
        if usa_metadata.get('companies_researched'):
            click.echo(f"  - Companies researched: {usa_metadata.get('companies_researched', 0)}")
            click.echo(f"  - Companies with DTx: {usa_metadata.get('companies_with_dtx', 0)}")
        
        # Count by FDA clearance type
        fda_counts = {}
        for dtx in usa_dtx_list:
            clearance = dtx.get("fda_clearance", "Unknown")
            fda_counts[clearance] = fda_counts.get(clearance, 0) + 1
        
        if fda_counts:
            click.echo("\nFDA Clearance Types:")
            for clearance, count in sorted(fda_counts.items()):
                click.echo(f"  - {clearance}: {count}")
    else:
        click.echo("\n=== USA DTx Data Status ===")
        click.echo("No USA DTx data found. Run 'scrape-usa' to collect data.")
    
    # === Evidence Data ===
    evidence_data = data_manager.load_evidence_data()
    evidence_meta = evidence_data.get("metadata", {})
    evidence_by_dtx = evidence_data.get("evidence_by_dtx", {})
    
    total_rct = evidence_meta.get("total_rct", 0)
    total_rwe = evidence_meta.get("total_rwe", 0)
    
    # If metadata counts aren't set, calculate them
    if total_rct == 0 and total_rwe == 0:
        for dtx_evidence in evidence_by_dtx.values():
            total_rct += len(dtx_evidence.get("RCT", []))
            total_rwe += len(dtx_evidence.get("RWE", []))
    
    click.echo(f"\n=== Evidence Papers ===")
    click.echo(f"  - DTx with evidence: {len(evidence_by_dtx)}")
    click.echo(f"  - Total RCT papers: {total_rct}")
    click.echo(f"  - Total RWE papers: {total_rwe}")
    click.echo(f"  - Last updated: {evidence_meta.get('last_updated', 'Never')}")


@cli.command()
@click.argument('text')
def translate(text: str):
    """Translate German text to English (test command)."""
    async def run():
        translator = Translator(source_lang="de", target_lang="en")
        result = await translator.translate(text)
        click.echo(f"Original: {text}")
        click.echo(f"Translated: {result}")
    
    asyncio.run(run())


if __name__ == "__main__":
    cli()
