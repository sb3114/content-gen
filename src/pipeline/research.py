"""
Research step: keyword analysis + competitor scraping, run in parallel.
After the SEO pipeline completes, SERP format detection is run against the
scraped pages and the result is packaged into keyword_review_data.
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
    db_settings=None,
) -> dict:
    """
    If a topic is provided, execute the 5-stage search-driven discovery pipeline.
    It automatically discovers top 3 competitor pages, scrapes them, extracts
    surviving keywords, and selects the focus Golden Ratio keyword.

    Returns a dict with keys:
      keyword_data, scraped_content, keyword_review_data
    """
    import logging
    logger = logging.getLogger(__name__)

    # Check if this is a topic-based blog discovery job (not a newsletter summary)
    is_newsletter = topic and ("Newsletter Summary" in topic or "Newsletter" in topic)
    if topic and not is_newsletter:
        logger.info(f"Triggering 5-stage SEO Keyword Discovery Pipeline for topic: '{topic}'")
        seo_data = await _researcher.run_seo_pipeline(topic, db_settings=db_settings)

        if seo_data.get("ok"):
            discovered_urls = seo_data.get("urls", [])
            # Combine discovered URLs with manual ones
            all_urls = list(set(competitor_urls + discovered_urls))

            logger.info(f"Scraping {len(all_urls)} competitor pages in parallel...")
            scraped_content = await _scraper.scrape_multiple(all_urls)

            # Run SERP format detection now that we have scraped pages
            chosen_keyword = seo_data.get("chosen_keyword", {})
            kw_name = chosen_keyword.get("keyword", topic)
            logger.info(f"Detecting SERP format for keyword: '{kw_name}'...")
            serp_format = await _researcher.detect_serp_format(kw_name, scraped_content)
            logger.info(f"SERP format detected: {serp_format.get('format')} (confidence: {serp_format.get('confidence')})")

            # Build keyword_review_data snapshot for the UI
            surviving = seo_data.get("surviving_keywords", [])
            keyword_review_data = {
                "serp_format": serp_format.get("format", "guide"),
                "serp_confidence": serp_format.get("confidence", "low"),
                "serp_examples": serp_format.get("examples", []),
                "chosen_keyword": chosen_keyword,
                "candidates": surviving[:10],  # top 10 survivors shown in UI
            }

            # Reformat to match keyword volumes structure for downstream components
            volumes_dict = {}
            for kw in seo_data["all_keywords_data"]:
                volumes_dict[kw["keyword"]] = {
                    "search_volume": kw["search_volume"],
                    "competition": kw["competition"],
                    "keyword_difficulty": kw["keyword_difficulty"]
                }

            return {
                "keyword_data": {
                    "ok": True,
                    "chosen_keyword": chosen_keyword,
                    "surviving_keywords": surviving,
                    "volumes": volumes_dict,
                    "discovery_meta": {
                        "domains": seo_data["domains"],
                        "urls": seo_data["urls"]
                    }
                },
                "scraped_content": scraped_content,
                "keyword_review_data": keyword_review_data,
            }
        else:
            logger.warning(f"SEO Pipeline fell back to standard lookup due to: {seo_data.get('error')}")

    # Standard research fallback path (no SERP format detection)
    keyword_data, scraped_content = await asyncio.gather(
        _researcher.research(seed_keywords, topic, db_settings=db_settings),
        _scraper.scrape_multiple(competitor_urls),
    )
    return {
        "keyword_data": keyword_data,
        "scraped_content": scraped_content,
        "keyword_review_data": None,
    }
