"""
Keyword research via pytrends (Google Trends) + DataForSEO (optional).
"""
import asyncio
import base64
from typing import Optional

import httpx

from src.config import settings


class KeywordResearcher:
    async def research(self, seed_keywords: list[str], topic: str) -> dict:
        """Run trends + volume lookups in parallel."""
        trends_task = asyncio.get_event_loop().run_in_executor(
            None, self._get_trends, seed_keywords
        )
        volume_task = self._get_dataforseo_volumes(seed_keywords)

        trends_data, volume_data = await asyncio.gather(
            trends_task, volume_task, return_exceptions=True
        )

        if isinstance(trends_data, Exception):
            trends_data = {"related_queries": [], "interest": {}}
        if isinstance(volume_data, Exception):
            volume_data = {}

        return {
            "seed_keywords": seed_keywords,
            "trends": trends_data,
            "volumes": volume_data,
            "related_queries": trends_data.get("related_queries", []),
        }

    def _get_trends(self, keywords: list[str]) -> dict:
        try:
            from pytrends.request import TrendReq

            pt = TrendReq(hl="en-US", tz=0)
            pt.build_payload(keywords[:5], timeframe="today 12-m")
            interest = pt.interest_over_time()
            related = pt.related_queries()

            related_list: list[str] = []
            for kw in keywords[:5]:
                if kw in related and related[kw].get("top") is not None:
                    top = related[kw]["top"]
                    related_list.extend(top["query"].tolist()[:5])

            return {
                "interest": interest.mean().to_dict() if not interest.empty else {},
                "related_queries": list(set(related_list))[:20],
            }
        except Exception as e:
            return {"related_queries": [], "error": str(e)}

    async def _get_dataforseo_volumes(self, keywords: list[str]) -> dict:
        if not (settings.dataforseo_login and settings.dataforseo_password):
            return {}

        creds = base64.b64encode(
            f"{settings.dataforseo_login}:{settings.dataforseo_password}".encode()
        ).decode()

        payload = [
            {
                "keywords": keywords,
                "language_name": "English",
                "location_code": 2840,  # United States
            }
        ]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live",
                json=payload,
                headers={"Authorization": f"Basic {creds}"},
            )
            if resp.status_code != 200:
                return {}

            data = resp.json()
            result = {}
            for item in data.get("tasks", [{}])[0].get("result", []):
                result[item["keyword"]] = {
                    "search_volume": item.get("search_volume"),
                    "competition": item.get("competition"),
                    "cpc": item.get("cpc"),
                }
            return result
