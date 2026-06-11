"""
Retrigger the planning + writing stages for a specific job.
Clears existing content_plan and article_markdown so the orchestrator
re-runs Steps 2 & 3 with the latest prompt logic.
"""
import asyncio
import os
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("retrigger")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

postgres_password = os.environ.get("POSTGRES_PASSWORD", "content")
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://content:{postgres_password}@localhost:5433/content_engine"

JOB_ID = "108cd4eb-020b-4126-b627-7076fc8bb4d7"


async def main():
    from src.database import AsyncSessionLocal
    from src.models.job import ArticleJob, JobStatus
    from src.models.settings import CompanySettings
    from src.schemas.content_plan import ContentPlan
    from src.pipeline.planning import run_planning
    from src.pipeline.writing import run_writing

    # 1. Load the job and company context
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, JOB_ID)
        if not job:
            logger.error("Job not found!")
            return

        settings_obj = await session.get(CompanySettings, 1)
        company_context = settings_obj.summarized_context if settings_obj else ""

        # Gather all the data we need before closing the session
        topic = job.topic
        focus_kw = job.confirmed_keyword or job.primary_keyword or ""
        secondary_kws = job.secondary_keywords or []
        kd = job.keyword_data or {}
        scraped = job.scraped_content or []
        krd = job.keyword_review_data or {}
        personalization = job.personalization_snippets or ""
        evaluation_metrics = job.evaluation_metrics or {}
        competitor_urls = job.competitor_urls or []

        logger.info(f"Job: {topic}")
        logger.info(f"Focus Keyword: {focus_kw}")
        logger.info(f"Secondary Keywords: {secondary_kws}")

    serp_format = krd.get("serp_format", "") if krd else ""
    logger.info(f"SERP Format from keyword_review_data: '{serp_format}'")

    # If no serp_format from keyword_review_data, check if topic/keyword suggests comparison
    check_text = (topic or "").lower() + " " + (focus_kw or "").lower()
    has_comparison_signals = any(w in check_text for w in ["vs", "compare", "comparison", "alternative"])
    logger.info(f"Comparison signals in topic/keyword: {has_comparison_signals}")

    # 2. Run Planning
    logger.info("=" * 60)
    logger.info("STEP 2: Running Planning Stage...")
    logger.info("=" * 60)

    from src.pipeline.summarize import get_published_memory
    published_memory = await get_published_memory()
    full_context = company_context + "\n" + published_memory

    plan, plan_usage = await run_planning(
        topic=topic,
        user_titles=[topic],
        keyword_data=kd,
        scraped_content=scraped,
        company_context=full_context,
        focus_keyword=focus_kw,
        serp_format=serp_format,
        secondary_keywords=secondary_kws,
        personalization_snippets=personalization,
    )

    logger.info(f"Planning complete! Title: '{plan.chosen_title}'")
    logger.info(f"Focus Keyword: {plan.focus_keyword}")
    logger.info(f"Outline sections: {len(plan.outline)}")
    for section in plan.outline:
        logger.info(f"  H2: {section.h2} | Intent: {section.intent[:80]}...")
    logger.info(f"Planning tokens: in={plan_usage['in']}, out={plan_usage['out']}")

    # 3. Run Writing
    logger.info("=" * 60)
    logger.info("STEP 3: Running Writing Stage...")
    logger.info("=" * 60)

    paa_list = evaluation_metrics.get("people_also_ask", [])
    if isinstance(paa_list, str):
        paa_list = [paa_list]

    comp_links = list(set(competitor_urls))

    article_md, write_usage, nano_banana_prompt = await run_writing(
        plan,
        company_context=full_context,
        personalization_snippets=personalization,
        people_also_ask=paa_list,
        competitor_urls=comp_links,
    )

    word_count = len(article_md.split())
    logger.info(f"Writing complete! Word count: {word_count}")
    logger.info(f"Nano Banana Prompt: {(nano_banana_prompt or '(none)')[:200]}")
    logger.info(f"Writing tokens: in={write_usage['in']}, out={write_usage['out']}")

    # 4. Validate links
    import re
    links = re.findall(r'<a\s+href=["\']([^"\']+)["\']', article_md)
    logger.info(f"Extracted {len(links)} hyperlinks:")
    for url in links:
        logger.info(f"  → {url}")

    # 5. Save back to DB
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, JOB_ID)
        if job:
            job.content_plan = plan.model_dump()
            job.article_markdown = article_md
            job.reviewed_markdown = article_md
            job.nano_banana_prompt = nano_banana_prompt
            job.input_tokens_used = (job.input_tokens_used or 0) + plan_usage["in"] + write_usage["in"]
            job.output_tokens_used = (job.output_tokens_used or 0) + plan_usage["out"] + write_usage["out"]
            session.add(job)
            await session.commit()
            logger.info(f"Saved updated plan + article back to job {JOB_ID}")

    logger.info("DONE!")


if __name__ == "__main__":
    asyncio.run(main())
