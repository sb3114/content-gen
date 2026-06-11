import asyncio
import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_pipeline_modifications")

# Ensure workspace root is in python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load dotenv
from dotenv import load_dotenv
load_dotenv()

# Override DATABASE_URL to target local exposed DB port 5433
postgres_password = os.environ.get("POSTGRES_PASSWORD", "content")
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://content:{postgres_password}@localhost:5433/content_engine"

from src.database import AsyncSessionLocal
from src.models.settings import CompanySettings
from src.pipeline.planning import run_planning
from src.pipeline.writing import run_writing
from src.schemas.content_plan import ContentPlan

async def test_planning_comparison():
    logger.info("=== Running Planning Stage Test (Comparison Format) ===")
    topic = "Smart assistive TV options for elderly dementia patients vs Komp"
    user_titles = ["Best Senior Assistive TVs"]
    keyword_data = {
        "chosen_keyword": {
            "keyword": "dementia smart tv alternatives",
            "secondary_keywords": ["assistive tv for seniors", "komp family alternatives", "jubilee tv cost"]
        }
    }
    
    # Run planning stage with comparison serp format
    plan, usage = await run_planning(
        topic=topic,
        user_titles=user_titles,
        keyword_data=keyword_data,
        scraped_content=[],
        company_context="BondNow is a simple senior-friendly photo frame and calling screen that connects elderly users with families. It focuses on warmth and dignity, avoiding clinical styling.",
        focus_keyword="dementia smart tv alternatives",
        serp_format="comparison",
        secondary_keywords=["assistive tv for seniors", "komp family alternatives", "jubilee tv cost"]
    )
    
    logger.info(f"Generated Title: {plan.chosen_title}")
    logger.info(f"Target Audience: {plan.target_audience}")
    logger.info(f"Meta Description: {plan.meta_description}")
    
    # Look for comparison or alternative providers in the outline
    comparison_found = False
    for section in plan.outline:
        text_to_check = (section.h2 + " " + " ".join(section.h3) + " " + section.intent).lower()
        if any(keyword in text_to_check for keyword in ["compare", "comparison", "alternative", "vs", "komp", "jubilee"]):
            comparison_found = True
            logger.info(f"Found comparison section: '{section.h2}' with intent: '{section.intent}'")
            
    assert comparison_found, "Failed: Expected comparison or competitor sections in the outline structure."
    logger.info("Planning Stage Test (Comparison Format) PASSED!")
    return plan

async def test_planning_guide():
    logger.info("=== Running Planning Stage Test (Guide Format) ===")
    topic = "How to help seniors stay socially connected online"
    user_titles = ["Seniors Staying Connected Online"]
    keyword_data = {
        "chosen_keyword": {
            "keyword": "how seniors stay connected online",
            "secondary_keywords": ["social tech for seniors", "elderly internet safety"]
        }
    }
    
    # Run planning stage with guide serp format
    plan, usage = await run_planning(
        topic=topic,
        user_titles=user_titles,
        keyword_data=keyword_data,
        scraped_content=[],
        company_context="BondNow is a simple senior-friendly photo frame and calling screen.",
        focus_keyword="how seniors stay connected online",
        serp_format="guide",
        secondary_keywords=["social tech for seniors", "elderly internet safety"]
    )
    
    logger.info(f"Generated Title: {plan.chosen_title}")
    
    # Assert that no competitor comparison section is forced
    comparison_found = False
    for section in plan.outline:
        text_to_check = (section.h2 + " " + " ".join(section.h3) + " " + section.intent).lower()
        if "komp" in text_to_check or "jubilee" in text_to_check:
            comparison_found = True
            
    assert not comparison_found, "Failed: Guide format should not include competitor comparisons like Komp or JubileeTV."
    logger.info("Planning Stage Test (Guide Format) PASSED!")
    return plan

async def test_writing_stage(plan: ContentPlan):
    logger.info("=== Running Writing Stage Test ===")
    
    company_context = (
        "BondNow is a warm, simple photo frame and video screen for seniors. "
        "It lets families send photos and video calls without the senior needing technical skills. "
        "No clinical designs, no surveillance vibes, preserving user dignity."
    )
    
    # Run the writing agent with search grounding enabled
    text, usage, nano_banana_prompt = await run_writing(
        plan=plan,
        company_context=company_context,
        personalization_snippets="My grandmother uses a basic tablet but gets confused. A photo frame style is much simpler.",
        people_also_ask=["What are alternatives to Komp family?", "How does JubileeTV compare to standard smart TVs?"],
        competitor_urls=["https://getjubileetv.com", "https://komp.family"]
    )
    
    logger.info(f"Generated Article Word Count: {len(text.split())}")
    logger.info(f"Nano Banana Prompt:\n{nano_banana_prompt}\n")
    
    # Validate HTML and URL rules
    import re
    # Find all anchor tags
    links = re.findall(r'<a\s+href=["\'](.*?)["\']>(.*?)</a>', text)
    logger.info(f"Extracted Hyperlinks ({len(links)}):")
    
    generic_homepages = [
        "https://www.alz.org", "https://www.mayoclinic.org", "https://www.nhs.uk",
        "https://www.who.int", "https://www.nia.nih.gov", "https://www.ageuk.org.uk",
        "https://alz.org", "https://mayoclinic.org", "https://nhs.uk", "https://who.int",
        "https://nia.nih.gov", "https://ageuk.org.uk"
    ]
    
    for url, anchor_text in links:
        logger.info(f"  - {anchor_text} -> {url}")
        # Strip trailing slashes
        clean_url = url.rstrip('/')
        assert clean_url not in generic_homepages, f"Failed: Found forbidden generic homepage citation: {url}"
        
    assert len(links) > 0, "Failed: Expected at least one verified resource hyperlink in the content."
    assert nano_banana_prompt is not None, "Failed: Expected a parsed Nano Banana prompt at the end of the writing stage."
    
    logger.info("Writing Stage Test PASSED!")

async def main():
    original_provider = "claude"
    
    # 1. Switch LLM provider to gemini
    async with AsyncSessionLocal() as session:
        s = await session.get(CompanySettings, 1)
        if s:
            original_provider = s.llm_provider
            logger.info(f"Temporarily switching LLM provider from {original_provider} to gemini...")
            s.llm_provider = "gemini"
            session.add(s)
            await session.commit()
            
    try:
        # 2. Test planning comparison
        comp_plan = await test_planning_comparison()
        
        # 3. Test planning guide
        await test_planning_guide()
        
        # 4. Test writing stage using the comparison plan
        await test_writing_stage(comp_plan)
        
        logger.info("ALL TESTS COMPLETED SUCCESSFULLY!")
    except Exception as e:
        logger.exception(f"Test run failed with error: {e}")
        sys.exit(1)
    finally:
        # 5. Restore original LLM provider
        async with AsyncSessionLocal() as session:
            s = await session.get(CompanySettings, 1)
            if s:
                logger.info(f"Restoring LLM provider back to {original_provider}...")
                s.llm_provider = original_provider
                session.add(s)
                await session.commit()

if __name__ == "__main__":
    asyncio.run(main())
