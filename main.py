#!/usr/bin/env python3
"""CLI entry point for DTx data scraping system."""
import asyncio
import click
from pathlib import Path

from scrapers import DiGAScraper
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
@click.option('--config', type=click.Path(exists=True), default='config/germany.json',
              help='Path to country configuration file')
def find_evidence(scrape_all: bool, dtx: str, config: str):
    """Find RCT/RWE evidence for DTx from PubMed and Google Scholar."""
    if not scrape_all and not dtx:
        click.echo("Error: Please specify --all or --dtx <name>")
        return
    
    click.echo("Starting evidence search...")
    
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
        
        try:
            for dtx_item in dtx_list:
                dtx_name = dtx_item.get("dtx_name", "Unknown")
                click.echo(f"\nSearching evidence for: {dtx_name}")
                
                evidence_list = await scraper.search_evidence(dtx_item)
                
                for evidence in evidence_list:
                    data_manager.add_evidence(dtx_name, evidence)
                    click.echo(f"  Found: {evidence.get('title', 'Unknown')[:60]}...")
                
                await asyncio.sleep(3)  # Rate limiting for search engines
            
            click.echo(f"\nEvidence saved to: {data_manager.evidence_file}")
            
        finally:
            await scraper.close()
    
    asyncio.run(run())


@cli.command()
def show_status():
    """Show current data status."""
    data_manager = DataManager()
    
    dtx_data = data_manager.load_dtx_data()
    metadata = dtx_data.get("metadata", {})
    
    click.echo("\n=== DTx Data Status ===")
    click.echo(f"Country: {metadata.get('country', 'Unknown')}")
    click.echo(f"Last updated: {metadata.get('last_updated', 'Never')}")
    click.echo(f"Total DTx: {metadata.get('total_count', 0)}")
    click.echo(f"  - Permanently listed: {metadata.get('active_count', 0)}")
    click.echo(f"  - Provisionally listed: {metadata.get('provisional_count', 0)}")
    click.echo(f"  - Delisted: {metadata.get('delisted_count', 0)}")
    
    evidence_data = data_manager.load_evidence_data()
    evidence_count = sum(
        len(v) for v in evidence_data.get("evidence_by_dtx", {}).values()
    )
    click.echo(f"\nTotal evidence papers: {evidence_count}")
    click.echo(f"DTx with evidence: {len(evidence_data.get('evidence_by_dtx', {}))}")


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
