"""
Competitor blog scraping via trafilatura + httpx.
"""
import asyncio
from typing import Optional

import httpx
import trafilatura


class ArticleScraper:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ContentEngine/1.0; "
            "+https://github.com/content-engine)"
        )
    }

    async def scrape(self, url: str) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(
                timeout=30, headers=self.HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                html = resp.text

            return await asyncio.get_event_loop().run_in_executor(
                None, self._extract, html, url
            )
        except Exception as e:
            return {"url": url, "error": str(e), "text": "", "title": ""}

    def _extract(self, html: str, url: str) -> dict:
        text = trafilatura.extract(
            html,
            include_tables=False,
            include_links=False,
            output_format="txt",
            no_fallback=False,
        )
        meta = trafilatura.extract_metadata(html)
        return {
            "url": url,
            "title": meta.title if meta else "",
            "author": meta.author if meta else "",
            "date": str(meta.date) if meta and meta.date else "",
            "text": text or "",
            "word_count": len((text or "").split()),
        }

    async def scrape_multiple(self, urls: list[str]) -> list[dict]:
        """Scrape all URLs concurrently."""
        tasks = [self.scrape(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            r for r in results
            if isinstance(r, dict) and r.get("text")
        ]
