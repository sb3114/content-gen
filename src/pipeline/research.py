"""
Research step: keyword analysis + competitor scraping, run in parallel.
"""
import asyncio

from src.integrations.keywords import KeywordResearcher
from src.integrations.scraper import ArticleScraper

_researcher = KeywordResearcher()
_scraper = ArticleScraper()


async def run_research(
    topic: str,
    seed_keywords: list[str],
    competitor_urls: list[str],
) -> dict:
    keyword_data, scraped_content = await asyncio.gather(
        _researcher.research(seed_keywords, topic),
        _scraper.scrape_multiple(competitor_urls),
    )
    return {"keyword_data": keyword_data, "scraped_content": scraped_content}
