"""
Stage 2: SERP Enrichment
Provides lightweight context, PAA questions, and SERP format classification
for all jobs (Standalone and Strategy) before they enter the Planning stage.
"""
import base64
import logging

from src.integrations.keywords import KeywordResearcher
from src.integrations.scraper import ArticleScraper
from src.config import settings

logger = logging.getLogger(__name__)

async def run_serp_enrichment(
    focus_keyword: str,
    existing_scraped: list[dict] = None,
    db_settings=None,
) -> dict:
    """
    Lightweight SERP enrichment. Costs ~1 DataForSEO API call + 1 Haiku LLM call.
    Returns: {serp_format, serp_confidence, paa_questions, scraped_content, competitor_structure}
    """
    researcher = KeywordResearcher()
    scraper = ArticleScraper()

    login = (db_settings.dataforseo_login if db_settings else None) or settings.dataforseo_login
    password = (db_settings.dataforseo_password if db_settings else None) or settings.dataforseo_password
    
    creds = None
    if login and password:
        creds = base64.b64encode(f"{login}:{password}".encode()).decode()

    # 1. Scrape competitor content if we don't already have it
    scraped_content = existing_scraped or []
    if not scraped_content and creds:
        logger.info(f"Discovering SERP URLs for '{focus_keyword}'...")
        urls, _ = await researcher.discover_competitors(focus_keyword, creds)
        if urls:
            logger.info(f"Scraping {len(urls[:5])} top ranking pages...")
            scraped_content = await scraper.scrape_multiple(urls[:5])

    # 2. Harvest PAA questions
    paa_questions = []
    if creds:
        logger.info(f"Harvesting PAA questions for '{focus_keyword}'...")
        paa_questions, _ = await researcher.harvest_serp_paa_and_validate(focus_keyword, creds)

    # 3. Classify SERP format
    logger.info(f"Classifying SERP format for '{focus_keyword}'...")
    serp_format = await researcher.detect_serp_format(focus_keyword, scraped_content)

    # 4. Extract competitor structure
    structure_summary = []
    for page in scraped_content[:3]:
        url = page.get("url", "unknown")
        word_count = len(page.get("text", "").split())
        h2s = page.get("h2", [])
        structure_summary.append({
            "url": url,
            "word_count": word_count,
            "h2_count": len(h2s),
            "h2_examples": h2s[:3]
        })

    return {
        "serp_format": serp_format.get("format", "guide"),
        "serp_confidence": serp_format.get("confidence", "low"),
        "paa_questions": paa_questions,
        "scraped_content": scraped_content,
        "competitor_structure": structure_summary,
    }
