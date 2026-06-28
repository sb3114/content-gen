"""
Stage 3.5: Citation Verification
Takes requested citations from the Planning stage, searches Google via DataForSEO,
verifies they are live (HTTP 200), and scrapes a summary context for the writer.
"""
import asyncio
import base64
import logging
import httpx

from src.config import settings

logger = logging.getLogger(__name__)

def is_homepage(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path or path.lower() in ["index.html", "index.htm", "index.php", "default.aspx"]:
            return True
    except Exception:
        pass
    return False

async def search_google_for_citation(query: str, creds: str) -> str:
    """Searches Google via DataForSEO for the query and returns the top URL."""
    payload = [{
        "keyword": query,
        "location_code": settings.dataforseo_location_code,
        "language_code": settings.dataforseo_language_code,
        "device": "desktop",
        "os": "windows"
    }]
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
                json=payload,
                headers={"Authorization": f"Basic {creds}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                tasks = data.get("tasks", [])
                if tasks and tasks[0].get("status_code") == 20000:
                    items = tasks[0].get("result", [{}])[0].get("items", [])
                    fallback_url = None
                    for item in items:
                        if item.get("type") == "organic" and item.get("url"):
                            url = item["url"]
                            if not fallback_url:
                                fallback_url = url
                            if not is_homepage(url):
                                return url
                    if fallback_url:
                        return fallback_url
    except Exception as e:
        logger.error(f"Error searching for citation '{query}': {e}")
    return ""

async def verify_and_fetch_citations(required_citations: list[str], db_settings=None) -> list[dict]:
    """
    Takes a list of citation queries, finds a working URL for each,
    and scrapes brief context.
    """
    if not required_citations:
        return []

    login = (db_settings.dataforseo_login if db_settings else None) or settings.dataforseo_login
    password = (db_settings.dataforseo_password if db_settings else None) or settings.dataforseo_password
    
    if not login or not password:
        logger.warning("No DataForSEO credentials available for citation verification.")
        return []
        
    creds = base64.b64encode(f"{login}:{password}".encode()).decode()
    verified = []
    
    from src.integrations.scraper import ArticleScraper
    scraper = ArticleScraper()
    
    for query in required_citations:
        logger.info(f"Verifying citation: '{query}'")
        url = await search_google_for_citation(query, creds)
        if url:
            logger.info(f"Found URL: {url}. Scraping context...")
            # Use our existing scraper which also verifies HTTP 200 implicitly
            scraped = await scraper.scrape_multiple([url])
            if scraped and scraped[0].get("text"):
                # We just need a summary context, not the whole massive page
                full_text = scraped[0]["text"]
                context_snippet = full_text[:1500] + ("..." if len(full_text) > 1500 else "")
                
                verified.append({
                    "query": query,
                    "url": url,
                    "title": scraped[0].get("title", ""),
                    "context": context_snippet
                })
                logger.info(f"Successfully verified citation for '{query}'.")
            else:
                logger.warning(f"Failed to scrape content from {url} (HTTP error or anti-bot).")
        else:
            logger.warning(f"No organic search results found for citation '{query}'.")
            
    return verified
