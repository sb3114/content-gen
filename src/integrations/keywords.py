"""
Keyword research via pytrends (Google Trends) + DataForSEO (optional).
"""
import asyncio
import base64
from typing import Optional

import httpx

from src.config import settings


class KeywordResearcher:
    async def research(self, seed_keywords: list[str], topic: str, db_settings=None) -> dict:
        """Run trends + volume lookups in parallel."""
        trends_task = asyncio.get_event_loop().run_in_executor(
            None, self._get_trends, seed_keywords
        )
        volume_task = self._get_dataforseo_volumes(seed_keywords, db_settings=db_settings)

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

    async def _get_dataforseo_volumes(self, keywords: list[str], db_settings=None) -> dict:
        login = (db_settings.dataforseo_login if db_settings else None) or settings.dataforseo_login
        password = (db_settings.dataforseo_password if db_settings else None) or settings.dataforseo_password

        if not (login and password):
            return {}

        creds = base64.b64encode(
            f"{login}:{password}".encode()
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

    async def validate_connection(self, db_settings=None) -> dict:
        """
        Verify connection to DataForSEO using /v3/appendix/user_data.
        """
        login = (db_settings.dataforseo_login if db_settings else None) or settings.dataforseo_login
        password = (db_settings.dataforseo_password if db_settings else None) or settings.dataforseo_password

        if not (login and password):
            return {"ok": False, "error": "Credentials missing. Please save them first."}

        creds = base64.b64encode(
            f"{login}:{password}".encode()
        ).decode()

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.dataforseo.com/v3/appendix/user_data",
                    headers={"Authorization": f"Basic {creds}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tasks = data.get("tasks", [])
                    if tasks and tasks[0].get("status_code") == 20000:
                        result = tasks[0].get("result", [{}])[0]
                        money_dict = result.get("money") or {}
                        try:
                            balance_val = float(money_dict.get("balance", 0.0))
                            total_val = float(money_dict.get("total", 0.0))
                        except (ValueError, TypeError):
                            balance_val = 0.0
                            total_val = 0.0
                        return {
                            "ok": True,
                            "balance": balance_val,
                            "total": total_val,
                            "login": login,
                        }
                    else:
                        error_msg = tasks[0].get("status_message") if tasks else "API error"
                        return {"ok": False, "error": error_msg}
                elif resp.status_code == 401:
                    return {"ok": False, "error": "Invalid credentials (Unauthorized)."}
                else:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            return {"ok": False, "error": f"Connection failed: {str(e)}"}

    def _get_root_domain(self, url_or_domain: str) -> str:
        from urllib.parse import urlparse
        if "://" in url_or_domain:
            domain = urlparse(url_or_domain).netloc
        else:
            domain = url_or_domain
        
        if domain.startswith("www."):
            domain = domain[4:]
        
        parts = domain.split('.')
        if len(parts) >= 2:
            if len(parts) >= 3 and parts[-2] in ["co", "org", "gov", "com", "net", "edu"]:
                return ".".join(parts[-3:])
            return ".".join(parts[-2:])
        return domain

    async def discover_competitors(self, topic: str, creds: str) -> tuple[list[str], list[str]]:
        """
        Stage 1: Competitor Discovery.
        Returns a tuple of:
          - list of top 3 organic page URLs
          - list of top 3 organic root domains
        """
        import logging
        logger = logging.getLogger(__name__)
        
        payload = [
            {
                "keyword": topic,
                "location_code": settings.dataforseo_location_code,
                "language_code": settings.dataforseo_language_code,
                "device": "desktop",
                "os": "windows"
            }
        ]
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
                    json=payload,
                    headers={"Authorization": f"Basic {creds}"},
                )
                if resp.status_code != 200:
                    logger.error(f"SERP API returned status {resp.status_code}: {resp.text}")
                    return [], []
                
                data = resp.json()
                tasks = data.get("tasks", [])
                if not tasks or tasks[0].get("status_code") != 20000:
                    logger.error(f"SERP API returned task error: {tasks}")
                    return [], []
                
                items = tasks[0].get("result", [{}])[0].get("items", [])
                organic_urls = []
                organic_domains = []
                
                # Exclude general authority platforms, encyclopedias, and generic portals
                EXCLUDED_DOMAINS = {
                    "amazon.com", "nytimes.com", "nih.gov", "wikipedia.org", "youtube.com",
                    "facebook.com", "pinterest.com", "ebay.com", "forbes.com", "walmart.com",
                    "mayoclinic.org", "webmd.com", "healthline.com", "cdc.gov", "medlineplus.gov",
                    "fda.gov", "who.int", "reddit.com", "quora.com", "glassdoor.com",
                    "indeed.com", "linkedin.com", "twitter.com", "instagram.com", "medium.com",
                    "github.com", "stackoverflow.com", "britannica.com", "investopedia.com"
                }
                
                for item in items:
                    if item.get("type") == "organic" and item.get("url"):
                        url = item["url"]
                        domain = item.get("domain") or self._get_root_domain(url)
                        root_domain = self._get_root_domain(domain)
                        
                        if root_domain.lower() in EXCLUDED_DOMAINS:
                            continue
                        
                        if root_domain not in organic_domains:
                            organic_urls.append(url)
                            organic_domains.append(root_domain)
                        
                        if len(organic_domains) >= 3:
                            break
                            
                return organic_urls, organic_domains
        except Exception as e:
            logger.error(f"Error in discover_competitors: {e}")
            return [], []

    def _is_clean_phrase(self, kw: str) -> bool:
        kw = kw.strip()
        if not kw:
            return False
            
        # 1. Skip keywords containing specific characters indicative of formulas, codes, drug dosages, or URLs
        for char in ['/', '\\', '%', '.', '=', '+', '_', ':', '?', '&']:
            if char in kw:
                return False
                
        # 2. Split words and check that we have at least one valid alphabetical word
        words = kw.split()
        if not words:
            return False
            
        has_alpha = False
        for w in words:
            clean_w = w.replace('-', '').replace("'", "").replace('"', '')
            if clean_w.isalpha():
                if len(clean_w) > 1 or clean_w.lower() in ['a', 'i']:
                    has_alpha = True
                    break
                    
        if not has_alpha:
            return False
            
        # 3. Filter out raw time coordinates, serial codes, and pure numbers
        lower_kw = kw.lower()
        if "military time" in lower_kw or "serial number" in lower_kw or "model" in lower_kw:
            return False
            
        return True

    async def scrape_competitor_keywords(self, domains: list[str], creds: str) -> list[str]:
        """
        Stage 2: Competitor Keyword Scrape & Filter.
        Queries Ranked Keywords for positions 1-5, de-brands, and deduplicates.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        all_keywords = set()
        
        for domain in domains:
            brand = domain.split('.')[0].lower()
            
            payload = [
                {
                    "target": domain,
                    "location_code": settings.dataforseo_location_code,
                    "language_name": settings.dataforseo_language_name,
                    "limit": 100
                }
            ]
            
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        "https://api.dataforseo.com/v3/dataforseo_labs/google/ranked_keywords/live",
                        json=payload,
                        headers={"Authorization": f"Basic {creds}"},
                    )
                    if resp.status_code != 200:
                        logger.error(f"Ranked Keywords returned status {resp.status_code} for {domain}")
                        continue
                    
                    data = resp.json()
                    tasks = data.get("tasks", [])
                    if not tasks or tasks[0].get("status_code") != 20000:
                        continue
                    
                    items = tasks[0].get("result", [{}])[0].get("items", [])
                    for item in items:
                        kw = item.get("keyword_data", {}).get("keyword") or item.get("keyword")
                        if not kw:
                            continue
                        
                        rank = item.get("ranked_serp_element", {}).get("serp_item", {}).get("rank_absolute")
                        if rank is None or not (1 <= rank <= 5):
                            continue
                        
                        kw_lower = kw.lower()
                        if brand in kw_lower or domain.lower() in kw_lower:
                            continue
                            
                        # Apply clean search phrase validation
                        if not self._is_clean_phrase(kw):
                            continue
                        
                        all_keywords.add(kw)
            except Exception as e:
                logger.error(f"Error scraping keywords for {domain}: {e}")
                
        return list(all_keywords)

    async def expand_keywords_universe(self, seeds: list[str], creds: str) -> list[dict]:
        """
        Stage 3: Keyword Universe Expansion.
        Calls Keyword Ideas to generate long-tail variations.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        if not seeds:
            return []
            
        payload = [
            {
                "keywords": seeds[:30],  # Max 30 seed keywords for faster live execution
                "location_code": settings.dataforseo_location_code,
                "language_name": settings.dataforseo_language_name,
                "limit": 100
            }
        ]
        
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    "https://api.dataforseo.com/v3/dataforseo_labs/google/keyword_ideas/live",
                    json=payload,
                    headers={"Authorization": f"Basic {creds}"},
                )
                if resp.status_code != 200:
                    logger.error(f"Keyword Ideas API returned {resp.status_code}: {resp.text}")
                    return []
                
                data = resp.json()
                tasks = data.get("tasks", [])
                if not tasks or tasks[0].get("status_code") != 20000:
                    return []
                
                items = tasks[0].get("result", [{}])[0].get("items", [])
                expanded_list = []
                
                for item in items:
                    kw = item.get("keyword")
                    if not kw:
                        continue
                    
                    info = item.get("keyword_info") or {}
                    props = item.get("keyword_properties") or {}
                    
                    search_volume = info.get("search_volume") or 0
                    competition = info.get("competition") or 0.0
                    difficulty = props.get("keyword_difficulty") or int(competition * 100)
                    
                    expanded_list.append({
                        "keyword": kw,
                        "search_volume": search_volume,
                        "competition": competition,
                        "keyword_difficulty": difficulty
                    })
                    
                return expanded_list
        except Exception as e:
            logger.error(f"Error in expand_keywords_universe: {e}")
            return []

    def _calculate_trend_slope(self, values: list[float]) -> float:
        if not values or len(values) < 2:
            return 0.0
        n = len(values)
        x = list(range(n))
        y = values
        
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xx = sum(val * val for val in x)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        
        denom = (n * sum_xx - sum_x * sum_x)
        if denom == 0:
            return 0.0
        slope = (n * sum_xy - sum_x * sum_y) / denom
        return slope

    async def verify_trends_pytrends(self, keywords: list[dict]) -> list[dict]:
        """
        Stage 4: Metric Sorting & Trend Verification.
        Filters KD <= 35, Search Volume >= 300, and verifies 90-day trend.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        filtered = [
            kw for kw in keywords 
            if kw["keyword_difficulty"] <= 35 and kw["search_volume"] >= 300
        ]
        
        if not filtered:
            return []
            
        filtered = sorted(filtered, key=lambda x: x["search_volume"], reverse=True)[:15]
        
        try:
            from pytrends.request import TrendReq
        except ImportError:
            logger.warning("pytrends is not installed, skipping trend slope filtering.")
            return filtered
            
        verified_keywords = []
        batches = [filtered[i:i+5] for i in range(0, len(filtered), 5)]
        
        for batch in batches:
            kw_names = [item["keyword"] for item in batch]
            try:
                loop = asyncio.get_event_loop()
                interest_df = await loop.run_in_executor(
                    None, self._fetch_pytrends_interest, kw_names
                )
                
                if interest_df is None or interest_df.empty:
                    verified_keywords.extend(batch)
                    continue
                
                for kw_item in batch:
                    name = kw_item["keyword"]
                    if name in interest_df.columns:
                        values = [float(v) for v in interest_df[name].tolist()]
                        slope = self._calculate_trend_slope(values)
                        
                        if slope < -0.5:
                            logger.info(f"Discarding declining keyword trend: {name} (slope: {slope:.3f})")
                            continue
                            
                        kw_item["trend_slope"] = slope
                        verified_keywords.append(kw_item)
                    else:
                        kw_item["trend_slope"] = 0.0
                        verified_keywords.append(kw_item)
            except Exception as e:
                logger.error(f"Error fetching trends for batch {kw_names}: {e}")
                verified_keywords.extend(batch)
                
        return verified_keywords

    def _fetch_pytrends_interest(self, keywords: list[str]):
        from pytrends.request import TrendReq
        try:
            pt = TrendReq(hl="en-US", tz=0)
            pt.build_payload(keywords, timeframe="today 3-m")
            return pt.interest_over_time()
        except Exception:
            return None

    async def select_golden_ratio_keyword(self, keywords: list[dict], brand_context: str = None) -> dict:
        """
        Stage 5: AI Keyword Selection.
        Selects the single best "Golden Ratio" keyword from survivors.
        """
        import json
        import google.generativeai as genai
        import logging
        logger = logging.getLogger(__name__)
        
        if not keywords:
            return {}
            
        kw_list_str = ""
        for kw in keywords:
            slope_str = f"{kw.get('trend_slope', 0.0):.3f}"
            kw_list_str += f"- Keyword: '{kw['keyword']}' | Search Volume: {kw['search_volume']} | KD: {kw['keyword_difficulty']} | Trend Slope: {slope_str}\n"
            
        brand_section = ""
        if brand_context:
            brand_section = f"""
We are selecting this focus keyword for our brand:
{brand_context}
"""

        prompt = f"""\
You are the Chief SEO Strategist for our brand.
Analyze the following list of keywords and select the SINGLE ONE keyword that represents the "Golden Ratio" combination:
- High Search Volume
- Low Keyword Difficulty (KD <= 35)
- Positive or Stable Search Trend Trajectory (Trend Slope)
{brand_section}

CRITICAL RELEVANCE RULES:
1. The chosen keyword MUST be highly relevant to our brand's mission, products, and target audience (ICPs).
2. It MUST NOT be a generic clinical medical term, database coordinate, generic consumer product, or raw engineering/system diagram.
3. It should directly relate to elderly social connection, family communication for seniors, smart/simple senior technology, caregiver peace of mind, dementia/elder care, or reducing social isolation. If none match senior care, select the most relevant high-intent general family/communication keyword.

List of surviving keywords:
{kw_list_str}

Return your choice in strict JSON format with exactly two keys:
{{
  "chosen_keyword": "the selected keyword",
  "rationale": "a brief, highly professional explanation of why this represents the absolute best Golden Ratio keyword and how it directly aligns with and serves our brand and target ICPs"
}}
"""
        try:
            model = genai.GenerativeModel(settings.gemini_planning_model)
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json"
                )
            )
            
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            
            result = json.loads(text)
            chosen_kw_name = result.get("chosen_keyword", "")
            for kw in keywords:
                if kw["keyword"].lower() == chosen_kw_name.lower():
                    kw["rationale"] = result.get("rationale", "")
                    return kw
                    
            first_kw = keywords[0]
            first_kw["rationale"] = result.get("rationale", "Selected as top candidate.")
            return first_kw
        except Exception as e:
            logger.error(f"Error in select_golden_ratio_keyword: {e}")
            top_kw = sorted(keywords, key=lambda x: x["search_volume"], reverse=True)[0]
            top_kw["rationale"] = "Safe fallback: highest search volume candidate."
            return top_kw

    async def detect_serp_format(self, keyword: str, scraped_pages: list[dict]) -> dict:
        """
        Classify the dominant content format Google is rewarding for this keyword.
        Uses page titles and first headings from already-scraped competitor pages.
        Returns: {format, confidence, examples}
        """
        import json
        import google.generativeai as genai
        import logging
        logger = logging.getLogger(__name__)

        if not scraped_pages:
            return {"format": "guide", "confidence": "low", "examples": []}

        titles_block = ""
        for page in scraped_pages[:5]:
            title = page.get("title", "")
            if title:
                titles_block += f'- "{title}"\n'

        if not titles_block:
            return {"format": "guide", "confidence": "low", "examples": []}

        prompt = f"""\
Given these top-ranking page titles for the keyword "{keyword}", classify the dominant content format.

Titles:
{titles_block}

Choose ONE format from: guide | list | comparison | how-to | tool | other

Return strict JSON only:
{{"format": "...", "confidence": "high|medium|low", "examples": ["title1", "title2", "title3"]}}
"""
        try:
            model = genai.GenerativeModel(settings.gemini_planning_model)
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    max_output_tokens=256,
                )
            )
            text = response.text.strip()
            return json.loads(text)
        except Exception as e:
            logger.warning(f"SERP format detection failed: {e}")
            return {"format": "guide", "confidence": "low", "examples": []}

    async def run_seo_pipeline(self, topic: str, db_settings=None) -> dict:
        """
        Orchestrator for the 5-Stage SEO Keyword Discovery Pipeline.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        login = (db_settings.dataforseo_login if db_settings else None) or settings.dataforseo_login
        password = (db_settings.dataforseo_password if db_settings else None) or settings.dataforseo_password
        
        if not (login and password):
            logger.error("DataForSEO credentials missing. SEO pipeline aborted.")
            return {"ok": False, "error": "Credentials missing."}
            
        creds = base64.b64encode(f"{login}:{password}".encode()).decode()
        
        logger.info(f"Stage 1: Discovering competitors for topic '{topic}'...")
        urls, domains = await self.discover_competitors(topic, creds)
        logger.info(f"Stage 1 Complete. Top competitor domains: {domains}")
        
        if not domains:
            return {"ok": False, "error": "No competitor domains discovered."}
            
        logger.info("Stage 2: Scraping competitor keywords in positions 1-5...")
        seeds = await self.scrape_competitor_keywords(domains, creds)
        logger.info(f"Stage 2 Complete. Found {len(seeds)} unique, de-branded seed terms.")
        
        if not seeds:
            return {"ok": False, "error": "No competitor keywords met position 1-5 bounds."}
            
        logger.info("Stage 3: Expanding keyword universe using Keyword Ideas...")
        expanded = await self.expand_keywords_universe(seeds, creds)
        logger.info(f"Stage 3 Complete. Generated {len(expanded)} long-tail keyword variations.")
        
        if not expanded:
            return {"ok": False, "error": "Keyword Ideas lookup returned no results."}
            
        logger.info("Stage 4: Filtering (KD <= 35, Vol >= 300) and running pytrends trajectory slope analysis...")
        verified = await self.verify_trends_pytrends(expanded)
        logger.info(f"Stage 4 Complete. {len(verified)} keywords survived metric & trend checks.")
        
        if not verified:
            return {"ok": False, "error": "No keywords met strict bounds (KD <= 35, Vol >= 300) and positive trend."}
            
        logger.info("Stage 5: Selecting the single 'Golden Ratio' Focus Keyword...")
        brand_context = None
        if db_settings:
            brand_context = f"Company Description: {db_settings.company_description}\nTarget Audience/ICPs:\n{db_settings.icp}"
            
        golden_ratio_kw = await self.select_golden_ratio_keyword(verified, brand_context=brand_context)
        logger.info(f"Stage 5 Complete. Golden Ratio keyword selected: '{golden_ratio_kw.get('keyword')}'")

        return {
            "ok": True,
            "urls": urls,
            "domains": domains,
            "chosen_keyword": golden_ratio_kw,
            "surviving_keywords": verified,
            "all_keywords_data": expanded,
        }
