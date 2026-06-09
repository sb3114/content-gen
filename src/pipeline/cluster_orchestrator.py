import json
import logging
from datetime import datetime, timedelta, time
from typing import List, Optional
import base64
from sqlmodel import select

from src.database import AsyncSessionLocal
from src.models.job import ClusterPlan, ArticleJob, JobStatus
from src.models.settings import CompanySettings
from src.integrations.keywords import KeywordResearcher
from src.pipeline.llm import call_llm

logger = logging.getLogger(__name__)

async def filter_relevant_competitor_keywords(
    keywords: list[dict],
    core_pillars_context: str,
    company_description: str,
    icp: str,
    db_settings
) -> list[dict]:
    """
    Invokes Claude to filter competitor keywords, removing those that are not relevant to our business context or ICP.
    """
    if not keywords:
        return []
    
    prompt = f"""\
You are an expert Brand & Product Strategist.
We have harvested organic keywords from a competitor's website. However, they may talk about topics or product lines that are NOT relevant to our business.
We must ONLY keep keywords that align with our brand pillars, product value proposition, and target ICP.

Our Brand Settings:
- Core Pillars & Messaging: {core_pillars_context}
- Company Description: {company_description}
- Target ICP (Ideal Customer Profile): {icp}

Here is the list of harvested competitor keywords:
{json.dumps([k["keyword"] for k in keywords], indent=2)}

Filter this list. For each keyword, determine if it is relevant to our business context (should we write content about it?).
Exclude keywords representing products, services, or technical areas we do not support (e.g., if a competitor sells TV remotes but we only sell communication software, exclude TV remote keywords).

Return a strict JSON object with a single key "relevant_keywords" containing a list of the approved keywords:
{{
  "relevant_keywords": ["keyword 1", "keyword 2"]
}}
"""
    try:
        from src.pipeline.llm import call_llm
        text, _ = await call_llm(
            prompt=prompt,
            tier="haiku",
            use_json=True,
            db_settings=db_settings
        )
        data = json.loads(text)
        approved = {k.lower().strip() for k in data.get("relevant_keywords", [])}
        filtered = [k for k in keywords if k["keyword"].lower().strip() in approved]
        return filtered
    except Exception as e:
        logger.error(f"Error in LLM competitor keyword filtering: {e}")
        # On fallback/error, return the original list to avoid breaking the job
        return keywords


async def run_cluster_plan_stage1(plan_id: str):
    """
    Agent 1: Keyword Research & Discovery Agent.
    Deconstructs company settings context pillars, queries keyword suggestions and SERP
    APIs (or triggers high-fidelity simulation if credentials missing),
    classifies into Hubs/Spokes, logs agent memory, and transitions to 'keyword_review'.
    """
    import os
    
    # 1. Quick read session (Zero connection starvation!)
    async with AsyncSessionLocal() as session:
        plan = await session.get(ClusterPlan, plan_id)
        if not plan:
            logger.error(f"Cluster plan {plan_id} not found for Stage 1.")
            return
        
        db_settings = await session.get(CompanySettings, 1)
        core_pillars_context = (db_settings.core_pillars if db_settings else None) or ""
        company_description = (db_settings.company_description if db_settings else None) or ""
        icp = (db_settings.icp if db_settings else None) or ""
        target_audience = (db_settings.target_audience if db_settings else None) or ""
        personas = (db_settings.personas if db_settings else None) or ""
        pain_points = (db_settings.pain_points if db_settings else None) or ""
        messaging_framework = (db_settings.messaging_framework if db_settings else None) or ""
        
        business_context_str = f"""Company Description: {company_description}
Target Ideal Customer Profile (ICP): {icp}
Target Audience: {target_audience}
Personas: {personas}
Pain Points: {pain_points}
Messaging Framework: {messaging_framework}
Core Content Pillars & Messaging Context: {core_pillars_context}"""
        
        plan_seed = plan.seed
        min_search_volume = plan.min_search_volume
        max_search_volume = plan.max_search_volume
        max_difficulty = plan.max_difficulty
        competitor_url = plan.competitor_url
        
        plan.status = "planning"
        plan.current_step = "keyword_research"
        session.add(plan)
        await session.commit()

    logger.info(f"Agent 1 starting keyword discovery for plan {plan_id} (Theme: '{plan_seed}')")

    if not plan_seed or plan_seed == "Brand Strategy":
        if not core_pillars_context.strip():
            async with AsyncSessionLocal() as session:
                p_obj = await session.get(ClusterPlan, plan_id)
                if p_obj:
                    p_obj.status = "failed"
                    p_obj.error_message = "Company core content pillars must be set in Brand Settings if no seed topic is provided."
                    session.add(p_obj)
                    await session.commit()
            raise ValueError("Company core content pillars must be set in Brand Settings if no seed topic is provided.")

    # 2. Long-Running Work (Completely outside database session)
    researcher = KeywordResearcher()
    discovered_pillars = []
    
    try:
        # Determine credentials
        from src.config import settings
        login = (db_settings.dataforseo_login if db_settings else None) or settings.dataforseo_login
        password = (db_settings.dataforseo_password if db_settings else None) or settings.dataforseo_password
        creds = base64.b64encode(f"{login}:{password}".encode()).decode() if (login and password) else None

        # 1. ALWAYS deconstruct company brand pillars first
        logger.info("Deconstructing core brand pillars using LLM...")
        seed_instruction = f'We have a content planning campaign seed theme: "{plan_seed}"'
        if plan_seed == "Brand Strategy":
            seed_instruction = "No specific campaign seed theme was provided; we are building our strategy directly from our core brand pillars."

        sys_instr = f"""You are an expert SEO Strategist.
{seed_instruction}

=== COMPANY BUSINESS CONTEXT ===
{business_context_str}
================================"""

        deconstruct_prompt = f"""\
Your task is to:
1. Deconstruct all major content pillars from the company context.
   You MUST deconstruct the exact core brand pillars provided in 'Core Content Pillars & Messaging Context'. Do NOT make up new pillars. Maintain their names as defined in the context.
2. For each identified content pillar, generate exactly 3 highly relevant, high-intent "short seed words/phrases" (of 2 to 4 words) to query for keyword ideas.

Return the result in strict JSON format matching this exact schema:
{{
  "pillars": [
    {{
      "pillar_name": "Name of Content Pillar (e.g. Senior Home Safety)",
      "seeds": ["elderly fall prevention", "smart home senior monitoring", "senior proofing home"]
    }}
  ]
}}
"""
        deconstruct_text, _ = await call_llm(
            prompt=deconstruct_prompt,
            tier="haiku",
            use_json=True,
            db_settings=db_settings
        )
        deconstruct_data = json.loads(deconstruct_text)
        pillars_list = deconstruct_data.get("pillars", [])

        # For each brand pillar, find high-quality keywords (using DataForSEO cached suggestions if credentials exist, falling back to Sonnet simulation if thin/empty)
        for p_item in pillars_list:
            p_name = p_item.get("pillar_name", "General Strategy")
            seeds = p_item.get("seeds", [])
            pillar_kws = []

            # A. Attempt Live DataForSEO keyword research
            if creds:
                for seed in seeds:
                    logger.info(f"Querying suggestions and harvesting PAA for seed '{seed}'...")
                    suggestions = await researcher.get_keyword_suggestions(seed, creds)
                    paa_questions, is_informational = await researcher.harvest_serp_paa_and_validate(seed, creds)

                    if not is_informational:
                        logger.info(f"Skipping seed '{seed}' due to commercial/transactional SERP footprint.")
                        continue

                    for sug in suggestions:
                        vol = sug.get("search_volume", 0)
                        diff = sug.get("keyword_difficulty", 0)

                        if min_search_volume <= vol <= max_search_volume and diff <= max_difficulty:
                            pillar_kws.append({
                                "keyword": sug["keyword"],
                                "search_volume": vol,
                                "keyword_difficulty": diff,
                                "secondary_keywords": [sug["keyword"]] + [seed],
                                "source": "discovery",
                                "paa_questions": paa_questions[:3]
                            })

            # B. If we have less than 4 keywords for this brand pillar, run the LLM fallback for this specific pillar!
            if len(pillar_kws) < 4:
                logger.info(f"Pillar '{p_name}' has only {len(pillar_kws)} keywords. Invoking high-fidelity LLM discovery to enrich...")
                sys_instr = f"""You are an advanced SEO discovery multi-agent pipeline simulating DataForSEO's Suggestions and Google SERP PAA harvesting engines.
=== COMPANY BUSINESS CONTEXT ===
{business_context_str}
================================"""
                
                fallback_prompt = f"""\
For our brand pillar "{p_name}", generate exactly 4 highly relevant, high-intent keywords (1 hub and 3 spokes) that perfectly match our brand strategy and our target ICP.
Ensure you also provide 3 "People Also Ask" questions for each.

We require keyword discovery with these exact bounds:
- Search Volume: between {min_search_volume} and {max_search_volume} searches/month.
- Keyword Difficulty (KD): strictly under {max_difficulty} (scale 0-100).
- Format Intent: informational blogs or guides.

Return your response in strict JSON matching this exact schema:
{{
  "keywords": [
    {{
      "keyword": "broad primary keyword",
      "search_volume": 850,
      "keyword_difficulty": 25,
      "role": "hub",
      "source": "discovery",
      "secondary_keywords": ["secondary keyword 1"],
      "paa_questions": [
        "What is the best safety technology for elderly at home?",
        "How do senior monitoring sensors work?",
        "What is the cheapest emergency response system?"
      ]
    }}
  ]
}}
"""
                fallback_text, _ = await call_llm(
                    prompt=fallback_prompt,
                    tier="sonnet",
                    system_instruction=sys_instr,
                    use_json=True,
                    db_settings=db_settings
                )
                try:
                    fb_data = json.loads(fallback_text)
                    fb_kws = fb_data.get("keywords", [])
                    # Append any unique keywords to avoid duplicates
                    existing_words = {k["keyword"].lower() for k in pillar_kws}
                    for fb_kw in fb_kws:
                        if fb_kw.get("keyword") and fb_kw["keyword"].lower() not in existing_words:
                            pillar_kws.append({
                                "keyword": fb_kw["keyword"],
                                "search_volume": fb_kw.get("search_volume") or 500,
                                "keyword_difficulty": fb_kw.get("keyword_difficulty") or 25,
                                "secondary_keywords": fb_kw.get("secondary_keywords") or [fb_kw["keyword"]],
                                "source": "discovery",
                                "paa_questions": fb_kw.get("paa_questions") or []
                            })
                except Exception as fb_err:
                    logger.error(f"Failed to parse LLM fallback keywords for pillar '{p_name}': {fb_err}")

            # C. Standardize and structure the pillar (1 hub, 3 spokes)
            if pillar_kws:
                pillar_kws = sorted(pillar_kws, key=lambda x: x["search_volume"], reverse=True)
                hub_item = pillar_kws[0]
                hub_item["role"] = "hub"
                hub_item["source"] = "discovery"
                
                spokes_items = pillar_kws[1:]
                for s in spokes_items:
                    s["role"] = "spoke"
                    s["source"] = "discovery"
                    
                discovered_pillars.append({
                    "pillar_name": p_name,
                    "keywords": [hub_item] + spokes_items[:3]
                })

        # 2. Add Competitor Analysis if requested
        if competitor_url:
            competitor_gap_kws = []
            competitor_spoke_kws = []
            logger.info(f"Performing competitor analysis for target '{competitor_url}'...")

            # A. Attempt Live DataForSEO competitor check
            comp_ranked = []
            if creds:
                try:
                    comp_ranked = await researcher.get_competitor_ranked_keywords(competitor_url, creds)
                except Exception as comp_err:
                    logger.error(f"Live DataForSEO competitor query failed: {comp_err}")
            
            # Filter competitor ranked keywords for KD < 40 and within volume limits
            filtered_comp = []
            for item in comp_ranked:
                vol = item.get("search_volume", 0)
                diff = item.get("keyword_difficulty", 0)
                if diff < 40 and min_search_volume <= vol <= max_search_volume:
                    filtered_comp.append(item)
            
            # Apply LLM Relevance Gate!
            if filtered_comp:
                filtered_comp = await filter_relevant_competitor_keywords(
                    keywords=filtered_comp,
                    core_pillars_context=core_pillars_context,
                    company_description=company_description,
                    icp=icp,
                    db_settings=db_settings
                )
            
            # Take top 4 relevant competitor keywords as Gaps and generate spokes
            if filtered_comp:
                filtered_comp = sorted(filtered_comp, key=lambda x: x.get("search_volume", 0), reverse=True)[:4]
                for c_item in filtered_comp:
                    kw_phrase = c_item["keyword"]
                    logger.info(f"Identified competitor gap keyword: '{kw_phrase}' (KD={c_item['keyword_difficulty']})")
                    
                    # Harvest PAA
                    paa_questions, is_informational = await researcher.harvest_serp_paa_and_validate(kw_phrase, creds)
                    competitor_gap_kws.append({
                        "keyword": kw_phrase,
                        "search_volume": c_item["search_volume"],
                        "keyword_difficulty": c_item["keyword_difficulty"],
                        "secondary_keywords": [kw_phrase],
                        "role": "hub",
                        "source": "competitor_gap",
                        "paa_questions": paa_questions[:3]
                    })
                    
                    # Semantic Extension (competitor spoke)
                    logger.info(f"Running semantic extension suggestions for competitor seed '{kw_phrase}'...")
                    extensions = await researcher.get_keyword_suggestions(kw_phrase, creds)
                    ext_count = 0
                    for ext in extensions:
                        if ext_count >= 2:
                            break
                        ext_vol = ext.get("search_volume", 0)
                        ext_diff = ext.get("keyword_difficulty", 0)
                        if ext_diff < 40 and min_search_volume <= ext_vol <= max_search_volume:
                            ext_paa, _ = await researcher.harvest_serp_paa_and_validate(ext["keyword"], creds)
                            competitor_spoke_kws.append({
                                "keyword": ext["keyword"],
                                "search_volume": ext_vol,
                                "keyword_difficulty": ext_diff,
                                "secondary_keywords": [ext["keyword"], kw_phrase],
                                "role": "spoke",
                                "source": "competitor_spoke",
                                "paa_questions": ext_paa[:3]
                            })
                            ext_count += 1

            # B. If no competitor keywords could be found/extracted via live DataForSEO, fall back to high-fidelity AI-based competitor gap check!
            if not competitor_gap_kws:
                logger.info("Live competitor extraction returned no results. Running AI-based competitor gap discovery fallback...")
                sys_instr = f"""You are an expert SEO Strategist simulating competitor gap discovery.
=== COMPANY BUSINESS CONTEXT ===
{business_context_str}
================================"""
                
                comp_prompt = f"""\
We have provided a competitor website: "{competitor_url}"
Analyze the likely topics they rank for, but focus PURELY on the intersection of our brand's ICP and business context.
Ignore topics that are not relevant to our business context (e.g. if the competitor discusses TV remotes, but we sell communication/elderly apps, ignore remotes; focus purely on overlapping communication and safety gaps).

Generate exactly 2 high-performing keywords driving their traffic that represent gaps we can fill (tag with "role": "hub", "source": "competitor_gap").
For each, feed it as a seed to find 2 related spoke keywords covering angles they missed (tag with "role": "spoke", "source": "competitor_spoke").
Provide simulated search volume (between {min_search_volume} and {max_search_volume}) and KD difficulty (< 40) along with 3 "People Also Ask" questions for each.

Return your response in strict JSON matching this exact schema:
{{
  "keywords": [
    {{
      "keyword": "elderly communication apps competitor",
      "search_volume": 650,
      "keyword_difficulty": 30,
      "role": "hub",
      "source": "competitor_gap",
      "secondary_keywords": ["senior communication device competitor"],
      "paa_questions": ["What is the easiest talk device for old people?", "How can I contact my grandma without a phone?"]
    }}
  ]
}}
"""
                comp_text, _ = await call_llm(
                    prompt=comp_prompt,
                    tier="sonnet",
                    system_instruction=sys_instr,
                    use_json=True,
                    db_settings=db_settings
                )
                try:
                    comp_data = json.loads(comp_text)
                    comp_kws = comp_data.get("keywords", [])
                    for kw in comp_kws:
                        if kw.get("role") == "hub":
                            competitor_gap_kws.append(kw)
                        else:
                            competitor_spoke_kws.append(kw)
                except Exception as comp_parse_err:
                    logger.error(f"Failed to parse AI competitor fallback: {comp_parse_err}")

            if competitor_gap_kws:
                discovered_pillars.append({
                    "pillar_name": "Competitor Gaps & Opportunities",
                    "keywords": competitor_gap_kws + competitor_spoke_kws
                })

        # Flatten discovered keywords
        keywords_payload = []
        for p_item in discovered_pillars:
            p_name = p_item.get("pillar_name", "General Strategy")
            for kw in p_item.get("keywords", []):
                keywords_payload.append({
                    "keyword": kw.get("keyword", ""),
                    "search_volume": kw.get("search_volume") or 500,
                    "keyword_difficulty": kw.get("keyword_difficulty") or 25,
                    "secondary_keywords": kw.get("secondary_keywords") or [],
                    "paa_questions": kw.get("paa_questions") or [],
                    "pillar": p_name,
                    "role": kw.get("role") or "spoke",
                    "source": kw.get("source") or "discovery",
                    "status": "approved"
                })

        # 3. Quick write session (Zero connection starvation!)
        async with AsyncSessionLocal() as session:
            p_obj = await session.get(ClusterPlan, plan_id)
            if p_obj:
                p_obj.keywords = keywords_payload
                p_obj.status = "keyword_review"
                p_obj.current_step = "idle"
                
                # Count deconstructed standard brand pillars (excluding competitor gap pillars)
                unique_pillars = {p.get("pillar_name") for p in discovered_pillars if p.get("pillar_name") != "Competitor Gaps & Opportunities"}
                if unique_pillars:
                    p_obj.num_pillars = len(unique_pillars)
                else:
                    p_obj.num_pillars = len(discovered_pillars)
                    
                session.add(p_obj)
                await session.commit()

        # Save Agent Memory Log to a beautiful markdown file
        os.makedirs("data/agent_memory", exist_ok=True)
        mem_path = f"data/agent_memory/plan_{plan_id}_keywords.md"
        
        md_content = f"""# Agent Memory: Discovered Keywords for Plan

- **Campaign Theme:** {plan_seed}
- **Date Created:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
- **Plan ID:** {plan_id}

---

"""
        grouped_mem = {}
        for kw in keywords_payload:
            p = kw["pillar"]
            if p not in grouped_mem:
                grouped_mem[p] = []
            grouped_mem[p].append(kw)

        for pillar_name, kws in grouped_mem.items():
            md_content += f"## Content Pillar: {pillar_name}\n\n"
            for kw in kws:
                role_badge = "★ HUB" if kw["role"] == "hub" else "● SPOKE"
                md_content += f"### {role_badge}: {kw['keyword']}\n"
                md_content += f"- **Search Volume:** {kw['search_volume']} /month\n"
                md_content += f"- **Keyword Difficulty:** {kw['keyword_difficulty']}/100\n"
                if kw.get("secondary_keywords"):
                    md_content += f"- **Supporting Terms:** {', '.join(kw['secondary_keywords'])}\n"
                if kw.get("paa_questions"):
                    md_content += "- **People Also Ask Questions:**\n"
                    for q in kw["paa_questions"]:
                        md_content += f"  - {q}\n"
                md_content += "\n"

        with open(mem_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        logger.info(f"Agent 1 complete. Discovered {len(keywords_payload)} keywords. Memory saved to {mem_path}.")

    except Exception as e:
        logger.exception(f"Error in Cluster Plan Stage 1: {e}")
        async with AsyncSessionLocal() as session:
            p_obj = await session.get(ClusterPlan, plan_id)
            if p_obj:
                p_obj.status = "failed"
                p_obj.error_message = str(e)
                session.add(p_obj)
                await session.commit()


async def run_cluster_plan_stage2(plan_id: str):
    """
    Agent 2 & 3: Strategy & Clustering Agent + Scheduling & Optimization Agent.
    Group approved keywords into core messaging pillars, build Hub & Spoke titles,
    allocate sequential calendar slots across 90-days, and transition to 'cluster_review'.
    """
    # 1. Quick read session (Zero connection starvation!)
    async with AsyncSessionLocal() as session:
        plan = await session.get(ClusterPlan, plan_id)
        if not plan:
            logger.error(f"Cluster plan {plan_id} not found for Stage 2.")
            return

        db_settings = await session.get(CompanySettings, 1)
        company_description = (db_settings.company_description if db_settings else None) or ""
        icp = (db_settings.icp if db_settings else None) or ""
        core_pillars_context = (db_settings.core_pillars if db_settings else None) or ""
        target_audience = (db_settings.target_audience if db_settings else None) or ""
        personas = (db_settings.personas if db_settings else None) or ""
        pain_points = (db_settings.pain_points if db_settings else None) or ""
        messaging_framework = (db_settings.messaging_framework if db_settings else None) or ""
        
        business_context_str = f"""Company Description: {company_description}
Target Ideal Customer Profile (ICP): {icp}
Target Audience: {target_audience}
Personas: {personas}
Pain Points: {pain_points}
Messaging Framework: {messaging_framework}
Core Content Pillars & Messaging Context: {core_pillars_context}"""
        
        plan_seed = plan.seed
        plan_keywords = list(plan.keywords or [])
        audience_split = plan.audience_split or []
        
        plan.status = "generating_clusters"
        plan.current_step = "strategy_generation"
        session.add(plan)
        await session.commit()

    # Filter approved keywords
    approved_kws = [kw for kw in plan_keywords if kw.get("status") == "approved"]
    if not approved_kws:
        approved_kws = [kw for kw in plan_keywords if kw.get("status") != "deleted"]

    if not approved_kws:
        async with AsyncSessionLocal() as session:
            p_obj = await session.get(ClusterPlan, plan_id)
            if p_obj:
                p_obj.status = "failed"
                p_obj.error_message = "No approved keywords found for cluster planning!"
                session.add(p_obj)
                await session.commit()
        raise ValueError("No approved keywords found for cluster planning!")

    logger.info(f"Agent 2 starting strategy grouping for plan {plan_id} with {len(approved_kws)} approved keywords")

    # Group approved keywords by content pillar
    pillars_map = {}
    for item in approved_kws:
        p_name = item.get("pillar", "General Strategy")
        if p_name not in pillars_map:
            pillars_map[p_name] = {"hub": None, "spokes": []}
        
        if item.get("role") == "hub":
            pillars_map[p_name]["hub"] = item
        else:
            pillars_map[p_name]["spokes"].append(item)

    # Format structural prompt input
    kws_str = ""
    for p_name, data in pillars_map.items():
        kws_str += f"### Pillar: {p_name}\n"
        hub = data["hub"]
        if hub:
            kws_str += f"- **HUB KEYWORD (Cornerstone):** '{hub['keyword']}' (Vol: {hub['search_volume']}, KD: {hub['keyword_difficulty']})\n"
            if hub.get("paa_questions"):
                kws_str += f"  - Google PAA Questions: {', '.join(hub['paa_questions'])}\n"
        for s in data["spokes"]:
            kws_str += f"- **SPOKE KEYWORD (Cluster Spoke):** '{s['keyword']}' (Vol: {s['search_volume']}, KD: {s['keyword_difficulty']})\n"
            if s.get("paa_questions"):
                kws_str += f"  - Google PAA Questions: {', '.join(s['paa_questions'])}\n"
        kws_str += "\n"

    sys_instr = f"""You are an expert Chief Strategy Officer and SEO Architect.
=== COMPANY BUSINESS CONTEXT ===
{business_context_str}
================================"""

    # Build audience split instruction if configured
    audience_split_section = ""
    if audience_split:
        split_lines = "\n".join(
            f"  - {entry['persona']}: {entry['percentage']}% of articles"
            for entry in audience_split
        )
        audience_split_section = f"""
AUDIENCE SPLIT DIRECTIVE:
The client has specified a target audience split for this content plan. You MUST distribute the total number of articles proportionally across these personas. For each article task, set the `"target_persona"` field to the persona it is written for.

Target Audience Distribution:
{split_lines}

Apply this split as closely as possible across all pillars. When rounding, favour the higher-percentage personas. Hub/Cornerstone articles may target the broadest or most strategic persona.
"""

    prompt = f"""\
Your task is to plan a comprehensive rolling 90-Day Hub & Spoke Content Matrix using the approved keyword pool below.
Seed Topic: "{plan_seed}"

Approved Content Pillars & Keywords Matrix:
{kws_str}
{audience_split_section}
CRITICAL STRUCTURAL RULES:
1. For each Pillar:
   - Create exactly 1 Cornerstone Hub article topic targeting the designated **HUB KEYWORD**. This must be a comprehensive, long-form guide. Set `"is_hub": true`.
   - Create exactly 1 supporting Spoke article topic for each designated **SPOKE KEYWORD**. Each spoke handles a single, granular question/sub-topic and MUST link back to the Hub. Set `"is_hub": false`.
2. Map the Google "People Also Ask" questions provided for each keyword directly into the article outline parameters.
3. Define the internal linking plan explicitly in the `"internal_linking_plan"` field (e.g. "Spoke links to Cornerstone Hub guide on X", or "Hub Cornerstone links to Y").
{"4. Assign `\"target_persona\"` to every task according to the AUDIENCE SPLIT DIRECTIVE above." if audience_split else ""}

Return your response in strict JSON format matching this exact schema:
{{
  "tasks": [
    {{
      "core_messaging_pillar": "Name of Content Pillar",
      "primary_keyword": "exact chosen keyword",
      "secondary_keywords": ["keyword idea 1", "keyword idea 2"],
      "topic": "An engaging, click-worthy article title",
      "is_hub": true,
      "target_persona": "{audience_split[0]['persona'] if audience_split else 'General'}",
      "internal_linking_plan": "Internal linking instructions (e.g., links back to Hub Cornerstone: [Hub Title])",
      "evaluation_metrics": {{
        "search_volume": 850,
        "keyword_difficulty": 32,
        "people_also_ask": [
          "PAA Question 1",
          "PAA Question 2",
          "PAA Question 3"
        ]
      }}
    }}
  ]
}}
"""
    try:
        text, _ = await call_llm(
            prompt=prompt,
            tier="sonnet",
            system_instruction=sys_instr,
            use_json=True,
            db_settings=db_settings
        )

        tasks_list = []
        try:
            data = json.loads(text)
            tasks_list = data.get("tasks", [])
        except Exception as parse_err:
            logger.error(f"Strategy generation parsing failed: {parse_err}. Retrying or throwing...")
            raise ValueError(f"Strategy Planner Agent returned invalid formatting: {parse_err}")

        if not tasks_list:
            raise ValueError("No content tasks were generated by the Strategy Agent.")

        logger.info("Agent 3 starting calendar distribution and optimization...")

        # 3. Quick write and read session to space enqueued dates
        async with AsyncSessionLocal() as session:
            p_obj = await session.get(ClusterPlan, plan_id)
            if not p_obj:
                return
            
            p_obj.current_step = "scheduling"
            session.add(p_obj)
            await session.commit()
            
            # Schedule spacing (1 task every 2 days to fit rolling 90 days cleanly, or 1 day if > 45 tasks)
            interval_days = 2 if len(tasks_list) <= 45 else 1

            now = datetime.utcnow()
            # Start scheduling tomorrow at 9:00 AM UTC
            current_date = datetime.combine(now.date() + timedelta(days=1), time(9, 0))

            # Fetch existing scheduled items to not conflict if possible
            stmt = select(ArticleJob.scheduled_at).where(
                ArticleJob.scheduled_at != None,
                ArticleJob.scheduled_at >= datetime.utcnow(),
                ArticleJob.status.in_([JobStatus.scheduled, JobStatus.queued])
            )
            res = await session.exec(stmt)
            scheduled_times = res.all()
            if scheduled_times:
                latest = max(scheduled_times)
                if latest > current_date:
                    current_date = datetime.combine(latest.date() + timedelta(days=1), time(9, 0))

            # Enrich tasks with the optimized scheduled dates
            enriched_tasks = []
            for idx, task in enumerate(tasks_list):
                task["scheduled_at"] = current_date.isoformat()
                enriched_tasks.append(task)
                current_date += timedelta(days=interval_days)

            p_obj.tasks = enriched_tasks
            p_obj.status = "cluster_review"
            p_obj.current_step = "idle"
            session.add(p_obj)
            await session.commit()
            
            logger.info(f"Stage 2 complete. Generated and scheduled {len(enriched_tasks)} content tasks. Paused at cluster_review.")

    except Exception as e:
        logger.exception(f"Error in Cluster Plan Stage 2: {e}")
        from src.pipeline.llm import LLMRateLimitException
        async with AsyncSessionLocal() as session:
            p_obj = await session.get(ClusterPlan, plan_id)
            if p_obj:
                if isinstance(e, LLMRateLimitException):
                    # Transient timeout / rate-limit: reset to retryable state so scheduler picks it up
                    p_obj.status = "generating_clusters"
                    p_obj.current_step = "strategy_generation"
                    p_obj.error_message = f"LLM timeout — will retry automatically. ({e})"
                    logger.warning(f"Stage 2 LLM timeout for plan {plan_id}. Resetting to generating_clusters for retry.")
                else:
                    p_obj.status = "failed"
                    p_obj.error_message = str(e)
                session.add(p_obj)
                await session.commit()



