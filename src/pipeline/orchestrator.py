import asyncio
import logging
import markdown as md
from datetime import datetime
from typing import Optional

from sqlmodel import select
from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus
from src.models.settings import CompanySettings
from src.pipeline.research import run_research
from src.pipeline.planning import run_planning
from src.pipeline.writing import run_writing
from src.pipeline.linkedin_adapt import run_linkedin_adaptation
from src.pipeline.newsletter_adapt import run_newsletter_adaptation
from src.schemas.content_plan import ContentPlan

logger = logging.getLogger(__name__)


async def _save(job_id: str, **kwargs):
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            job.updated_at = datetime.utcnow()
            session.add(job)
            await session.commit()


from urllib.parse import urlparse

def get_site_title(wp_site_url: str) -> str:
    if not wp_site_url:
        return "BondNow"
    try:
        parsed = urlparse(wp_site_url)
        host = parsed.netloc or parsed.path
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".")
        if parts:
            return parts[0].capitalize()
    except Exception:
        pass
    return "BondNow"


async def generate_punchy_seo_title(title: str, focus_keyword: str, site_title: str, settings_obj) -> str:
    from google import genai
    api_key = settings_obj.gemini_api_key if settings_obj else None
    if not api_key:
        from src.config import settings as app_settings
        api_key = app_settings.gemini_api_key
    
    if not api_key:
        limit = 60 - 3 - len(site_title)
        if len(title) > limit:
            return title[:limit].rstrip()
        return title

    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""You are an SEO expert. Generate a short, punchy SEO title tag under {60 - 3 - len(site_title)} characters for this blog article:
        H1 Title: {title}
        Focus Keyword: {focus_keyword}
        
        Rules:
        1. The generated title MUST be strictly less than {60 - 3 - len(site_title)} characters.
        2. It MUST include the focus keyword or main keywords from the title.
        3. It must be highly engaging and click-worthy.
        4. Return ONLY the title text itself without any quotes or explanations.
        """
        model_name = "gemini-2.0-flash"
        if settings_obj and hasattr(settings_obj, "gemini_planning_model") and settings_obj.gemini_planning_model:
            model_name = settings_obj.gemini_planning_model
        else:
            from src.config import settings as app_settings
            if hasattr(app_settings, "gemini_planning_model") and app_settings.gemini_planning_model:
                model_name = app_settings.gemini_planning_model
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        seo_title = response.text.strip().strip('"').strip("'")
        limit = 60 - 3 - len(site_title)
        if len(seo_title) > limit:
            seo_title = seo_title[:limit].rstrip()
        return seo_title
    except Exception as e:
        logger.warning(f"Failed to generate punchy SEO title: {e}. Falling back to truncated title.")
        limit = 60 - 3 - len(site_title)
        if len(title) > limit:
            return title[:limit].rstrip()
        return title


# ── Phase 1: Research → Keyword Gate ─────────────────────────────────────────

async def run_pipeline(job_id: str) -> None:
    """
    Phase 1 of 2: Research → Keyword Gate.

    Runs the 5-stage SEO pipeline, scrapes competitor pages, detects SERP
    format, and then either:
      - Pauses at pending_review (step=keyword_confirmation) for user to confirm the keyword, OR
      - Immediately calls resume_pipeline() if auto_approve=True (fully touchless).

    Supports resuming from a partial state (e.g. after a crash).
    """
    logger.info(f"Starting Phase 1 (Research & Keyword Gate) for Job ID {job_id}...")
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database.")
                return

            if job.status == JobStatus.paused:
                logger.info(f"[PAUSED] Job {job_id} is paused. Aborting Phase 1.")
                return

            if job.primary_keyword:
                logger.info(f"Job {job_id} already has primary keyword '{job.primary_keyword}'. Moving directly to Phase 2.")
                if not job.confirmed_keyword:
                    job.confirmed_keyword = job.primary_keyword
                if not job.keyword_data:
                    job.keyword_data = {
                        "chosen_keyword": {
                            "keyword": job.primary_keyword,
                            "secondary_keywords": job.secondary_keywords or [],
                            "search_volume": job.evaluation_metrics.get("search_volume") if job.evaluation_metrics else 0,
                            "keyword_difficulty": job.evaluation_metrics.get("keyword_difficulty") if job.evaluation_metrics else 0,
                            "trend_slope": job.evaluation_metrics.get("trend_slope", 0.0) if job.evaluation_metrics else 0.0
                        },
                        "ok": True
                    }
                session.add(job)
                await session.commit()
                await resume_pipeline(job_id)
                return

            topic = job.topic
            user_titles = job.user_titles
            competitor_urls = job.competitor_urls
            seed_keywords = job.seed_keywords
            publish_linkedin = job.publish_linkedin
            publish_newsletter = job.publish_newsletter
            auto_approve = job.auto_approve

            # Initialize tokens from current job state (in case of resume)
            input_tokens = job.input_tokens_used or 0
            output_tokens = job.output_tokens_used or 0

            # Fetch company context from persistent brand memory cache
            from src.pipeline.memory import load_brand_context_memory
            brand_ctx = load_brand_context_memory()
            company_context = brand_ctx.get("summarized_context") or ""

            # Fetch Published Memory (Cross-linking context)
            from src.pipeline.summarize import get_published_memory
            published_memory = await get_published_memory()
            full_context = company_context + "\n" + published_memory

        # ── Decision: newsletter summary bypasses keyword gate ────────────────
        is_newsletter_summary = (job.newsletter_type == "summary" and publish_newsletter)

        # ── Step 1: Research ──────────────────────────────────────────────────
        if not is_newsletter_summary and (not job.keyword_data or not job.scraped_content):
            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused before Step 1: Research. Aborting.")
                    return

            logger.info(f"Job {job_id}: Launching Step 1 (Research)...")
            await _save(job_id, status=JobStatus.running, current_step="research")
            research_data = await run_research(
                topic, seed_keywords, competitor_urls, db_settings=settings_obj
            )

            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused after Research. Saving scraped data and aborting.")
                    await _save(
                        job_id,
                        keyword_data=research_data["keyword_data"],
                        scraped_content=research_data["scraped_content"],
                        keyword_review_data=research_data.get("keyword_review_data"),
                    )
                    return

            # Calculate next open slot sequentially if a golden ratio keyword was chosen
            kw_data = research_data.get("keyword_data", {})
            scheduled_dt = job.scheduled_at
            seed_kws = job.seed_keywords

            if kw_data and kw_data.get("chosen_keyword"):
                chosen_kw_dict = kw_data["chosen_keyword"]
                chosen_kw = chosen_kw_dict.get("keyword")
                if chosen_kw:
                    seed_kws = [chosen_kw]

                if not scheduled_dt:
                    from src.pipeline.scheduling import get_next_open_slot
                    async with AsyncSessionLocal() as session:
                        scheduled_dt = await get_next_open_slot(session)

            await _save(
                job_id,
                current_step="planning",
                keyword_data=research_data["keyword_data"],
                scraped_content=research_data["scraped_content"],
                keyword_review_data=research_data.get("keyword_review_data"),
                scheduled_at=scheduled_dt,
                seed_keywords=seed_kws,
            )
            research_data_dict = research_data
            logger.info(f"Job {job_id}: Step 1 (Research) completed. Discovered Focus Keyword: '{kw_data.get('chosen_keyword', {}).get('keyword')}'")
        elif not is_newsletter_summary:
            research_data_dict = {
                "keyword_data": job.keyword_data,
                "scraped_content": job.scraped_content,
                "keyword_review_data": job.keyword_review_data,
            }
        else:
            research_data_dict = {"keyword_data": None, "scraped_content": None, "keyword_review_data": None}

        # ── Step 1.5: Viability Check (Token Optimization) ───────────────────
        if not is_newsletter_summary and research_data_dict["keyword_data"]:
            if research_data_dict["keyword_data"].get("ok") and research_data_dict["keyword_data"].get("chosen_keyword"):
                is_viable = True
            else:
                volumes = research_data_dict["keyword_data"].get("volumes", {})
                is_viable = any(
                    (v.get("competition", 1.0) * 100 <= 30) and (v.get("search_volume", 0) >= 500)
                    for v in volumes.values()
                )

            if not is_viable and not job.content_plan:
                msg = "Token Optimization: No keywords met the threshold (KD <= 35 & Volume >= 300). Job stopped to save resources."
                logger.warning(f"Job {job_id} failed viability check: {msg}")
                await _save(job_id, status=JobStatus.failed, error_message=msg)
                return

        # ── Keyword Gate ──────────────────────────────────────────────────────
        # auto_approve=True → skip gate, use AI-chosen keyword directly
        # auto_approve=False → pause for user confirmation (unless plan already exists)
        if not is_newsletter_summary and not job.content_plan:
            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused before Keyword Gate. Aborting.")
                    return

            if auto_approve:
                # Set confirmed_keyword to the AI-chosen keyword and go straight to Phase 2
                kw_data = research_data_dict.get("keyword_data") or {}
                chosen = kw_data.get("chosen_keyword", {})
                auto_kw = chosen.get("keyword", "") if isinstance(chosen, dict) else ""
                logger.info(f"Job {job_id}: Skipping keyword gate (auto_approve=True). Confirmed keyword: '{auto_kw}'")
                await _save(job_id, confirmed_keyword=auto_kw)
                await resume_pipeline(job_id)
                return
            else:
                # Pause here — UI will show the keyword review panel
                logger.info(f"Job {job_id}: Paused at Keyword review gate for user confirmation.")
                await _save(
                    job_id,
                    status=JobStatus.pending_review,
                    current_step="keyword_confirmation",
                )
                return

    except Exception as exc:
        from src.pipeline.llm import LLMRateLimitException
        if isinstance(exc, LLMRateLimitException):
            async with AsyncSessionLocal() as session:
                settings_obj = await session.get(CompanySettings, 1)
                if not settings_obj:
                    settings_obj = CompanySettings(id=1)
                
                from datetime import timedelta
                retry_time = datetime.utcnow() + timedelta(seconds=exc.retry_after_seconds)
                settings_obj.rate_limit_banner = f"LLM rate limit reached. Next retry automatically scheduled at {retry_time.strftime('%H:%M:%S')} UTC."
                settings_obj.rate_limit_until = retry_time
                session.add(settings_obj)
                
                job = await session.get(ArticleJob, job_id)
                if job:
                    job.status = JobStatus.queued
                    job.queue_position = 1
                    session.add(job)
                await session.commit()
            logger.warning(f"Job {job_id} hit LLM rate limit in Phase 1. Scheduled to retry in {exc.retry_after_seconds}s at {retry_time} UTC.")
            return

        logger.error(f"Job {job_id} failed in Phase 1 (Research/Gate) stage '{job.current_step if job else 'unknown'}': {exc}", exc_info=True)
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if job:
                await _save(
                    job_id,
                    status=JobStatus.failed,
                    error_message=str(exc),
                    error_step=job.current_step,
                )
        raise


# ── Phase 2: Write → Adapt → (Auto-)Approve ──────────────────────────────────

async def resume_pipeline(job_id: str) -> None:
    """
    Phase 2 of 2: Plan → Write → Adapt → Review or Auto-Publish.

    Called either by the confirm-keyword API endpoint (user confirmed keyword)
    or directly from run_pipeline() when auto_approve=True.
    """
    logger.info(f"Resuming Phase 2 (Planning & Generation) for Job ID {job_id}...")
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database.")
                return

            if job.status == JobStatus.paused:
                logger.info(f"[PAUSED] Job {job_id} is paused. Aborting Phase 2.")
                return

            topic = job.topic
            user_titles = job.user_titles
            publish_linkedin = job.publish_linkedin
            publish_newsletter = job.publish_newsletter
            auto_approve = job.auto_approve
            confirmed_keyword = job.confirmed_keyword

            input_tokens = job.input_tokens_used or 0
            output_tokens = job.output_tokens_used or 0

            settings_obj = await session.get(CompanySettings, 1)
            company_context = settings_obj.summarized_context if settings_obj else ""

            from src.pipeline.summarize import get_published_memory
            published_memory = await get_published_memory()
            full_context = company_context + "\n" + published_memory

        is_newsletter_summary = (job.newsletter_type == "summary" and publish_newsletter)

        # Determine the focus keyword: user-confirmed > AI-chosen > seed
        focus_kw = confirmed_keyword or ""
        if not focus_kw:
            async with AsyncSessionLocal() as session:
                refreshed_job = await session.get(ArticleJob, job_id)
                focus_kw = refreshed_job.seed_keywords[0] if (refreshed_job and refreshed_job.seed_keywords) else ""

        # Pull SERP format and secondary keywords to inject into planning
        async with AsyncSessionLocal() as session:
            refreshed = await session.get(ArticleJob, job_id)
            kd = refreshed.keyword_data or {}
            scraped = refreshed.scraped_content or []
            krd = refreshed.keyword_review_data or {}

        serp_format = krd.get("serp_format", "") if krd else ""
        
        # Extract the suggested 5 secondary keywords from the job columns or keyword discovery stage
        secondary_kws = refreshed.secondary_keywords or []
        if not secondary_kws and kd and isinstance(kd, dict):
            chosen_kw_dict = kd.get("chosen_keyword") or {}
            if isinstance(chosen_kw_dict, dict):
                secondary_kws = chosen_kw_dict.get("secondary_keywords") or []

        # ── Step 2: Planning ──────────────────────────────────────────────────
        if not is_newsletter_summary and not job.content_plan:
            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused before Step 2: Planning. Aborting.")
                    return

            logger.info(f"Job {job_id}: Launching Step 2 (Planning)...")
            await _save(job_id, status=JobStatus.running, current_step="planning")

            plan, plan_usage = await run_planning(
                topic=topic,
                user_titles=user_titles,
                keyword_data=kd,
                scraped_content=scraped,
                company_context=full_context,
                focus_keyword=focus_kw,
                serp_format=serp_format,
                secondary_keywords=secondary_kws,
                personalization_snippets=job.personalization_snippets or "",
            )
            input_tokens += plan_usage["in"]
            output_tokens += plan_usage["out"]

            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused after Planning. Saving plan and aborting.")
                    await _save(
                        job_id,
                        content_plan=plan.model_dump(),
                        input_tokens_used=input_tokens,
                        output_tokens_used=output_tokens,
                    )
                    return

            await _save(
                job_id,
                current_step="writing",
                content_plan=plan.model_dump(),
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
            logger.info(f"Job {job_id}: Step 2 (Planning) completed. Chosen title: '{plan.chosen_title}'")
        elif not is_newsletter_summary:
            plan = ContentPlan(**job.content_plan)
        else:
            plan = None

        # ── Step 3: Writing ───────────────────────────────────────────────────
        if not is_newsletter_summary and not job.article_markdown:
            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused before Step 3: Writing. Aborting.")
                    return

            logger.info(f"Job {job_id}: Launching Step 3 (Writing)...")
            await _save(job_id, status=JobStatus.running, current_step="writing")
            
            # Extract People Also Ask questions
            paa_list = []
            if refreshed.evaluation_metrics and isinstance(refreshed.evaluation_metrics, dict):
                paa = refreshed.evaluation_metrics.get("people_also_ask")
                if paa:
                    if isinstance(paa, list):
                        paa_list = paa
                    elif isinstance(paa, str):
                        paa_list = [paa]

            # Gather and deduplicate competitor URLs to pass to writing agent
            comp_links = []
            if refreshed.competitor_urls:
                for url in refreshed.competitor_urls:
                    if url and url not in comp_links:
                        comp_links.append(url)
            if refreshed.scraped_content:
                for item in refreshed.scraped_content:
                    url = item.get("url")
                    if url and url not in comp_links:
                        comp_links.append(url)

            article_md, write_usage = await run_writing(
                plan,
                company_context=full_context,
                personalization_snippets=job.personalization_snippets or "",
                people_also_ask=paa_list,
                competitor_urls=comp_links,
            )
            input_tokens += write_usage["in"]
            output_tokens += write_usage["out"]

            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused after Writing. Saving article and aborting.")
                    await _save(
                        job_id,
                        article_markdown=article_md,
                        reviewed_markdown=article_md,
                        input_tokens_used=input_tokens,
                        output_tokens_used=output_tokens,
                    )
                    return

            await _save(
                job_id,
                current_step="linkedin" if publish_linkedin else ("newsletter" if publish_newsletter else None),
                article_markdown=article_md,
                reviewed_markdown=article_md,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
            logger.info(f"Job {job_id}: Step 3 (Writing) completed successfully. Word count: {len(article_md.split())}")
        else:
            article_md = job.article_markdown or ""

        # ── Step 4: LinkedIn Adaptation ───────────────────────────────────────
        if not is_newsletter_summary and publish_linkedin and not job.linkedin_post:
            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused before Step 4: LinkedIn adaptation. Aborting.")
                    return

            logger.info(f"Job {job_id}: Launching Step 4 (LinkedIn Adaptation)...")
            await _save(job_id, status=JobStatus.running, current_step="linkedin")
            li_post, li_usage = await run_linkedin_adaptation(plan, article_md)
            input_tokens += li_usage["in"]
            output_tokens += li_usage["out"]

            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused after LinkedIn adaptation. Saving post and aborting.")
                    await _save(
                        job_id,
                        linkedin_post=li_post.full_text,
                        reviewed_linkedin=li_post.full_text,
                        input_tokens_used=input_tokens,
                        output_tokens_used=output_tokens,
                    )
                    return

            await _save(
                job_id,
                current_step="newsletter" if publish_newsletter else None,
                linkedin_post=li_post.full_text,
                reviewed_linkedin=li_post.full_text,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
            logger.info(f"Job {job_id}: Step 4 (LinkedIn Adaptation) completed successfully.")
        else:
            li_text = job.linkedin_post or ""

        # ── Step 5: Newsletter Adaptation ─────────────────────────────────────
        if publish_newsletter and not job.newsletter_html:
            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused before Step 5: Newsletter adaptation. Aborting.")
                    return

            logger.info(f"Job {job_id}: Launching Step 5 (Newsletter Adaptation)...")
            await _save(job_id, status=JobStatus.running, current_step="newsletter")
            nl_data, nl_usage = await run_newsletter_adaptation(job_id, plan, article_md)
            input_tokens += nl_usage["in"]
            output_tokens += nl_usage["out"]

            # Check pause status
            async with AsyncSessionLocal() as session:
                refreshed = await session.get(ArticleJob, job_id)
                if refreshed and refreshed.status == JobStatus.paused:
                    logger.info(f"[PAUSED] Job {job_id} is paused after Newsletter adaptation. Saving newsletter HTML and aborting.")
                    await _save(
                        job_id,
                        newsletter_subject=nl_data.subject,
                        newsletter_preheader=nl_data.preheader,
                        newsletter_html=nl_data.body_html,
                        reviewed_newsletter_subject=nl_data.subject,
                        reviewed_newsletter_preheader=nl_data.preheader,
                        reviewed_newsletter_html=nl_data.body_html,
                        input_tokens_used=input_tokens,
                        output_tokens_used=output_tokens,
                    )
                    return

            await _save(
                job_id,
                newsletter_subject=nl_data.subject,
                newsletter_preheader=nl_data.preheader,
                newsletter_html=nl_data.body_html,
                reviewed_newsletter_subject=nl_data.subject,
                reviewed_newsletter_preheader=nl_data.preheader,
                reviewed_newsletter_html=nl_data.body_html,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
            logger.info(f"Job {job_id}: Step 5 (Newsletter Adaptation) completed successfully.")

        # Generate the 3 image candidates for the review gate
        try:
            from src.pipeline.image_gen import generate_images_for_job
            logger.info(f"Job {job_id}: Generating 3 image candidates...")
            async with AsyncSessionLocal() as session:
                db_job = await session.get(ArticleJob, job_id)
                db_settings = await session.get(CompanySettings, 1)
            images = await generate_images_for_job(db_job, db_settings=db_settings)
            await _save(job_id, generated_images=images)
            if images:
                await _save(job_id, selected_image=images[0])
        except Exception as img_err:
            logger.error(f"Job {job_id}: Failed to generate initial images: {img_err}")

        # ── Gate: Auto-approve or Human Review ────────────────────────────────
        if auto_approve:
            logger.info(f"Job {job_id}: Skipping human review (auto_approve=True). Launching publication...")
            await _save(job_id, status=JobStatus.approved, current_step=None)
            await publish_job(job_id)
        else:
            logger.info(f"Job {job_id}: Core processing complete. Paused for human content review.")
            await _save(job_id, status=JobStatus.pending_review, current_step=None)

    except Exception as exc:
        from src.pipeline.llm import LLMRateLimitException
        if isinstance(exc, LLMRateLimitException):
            async with AsyncSessionLocal() as session:
                settings_obj = await session.get(CompanySettings, 1)
                if not settings_obj:
                    settings_obj = CompanySettings(id=1)
                
                from datetime import timedelta
                retry_time = datetime.utcnow() + timedelta(seconds=exc.retry_after_seconds)
                settings_obj.rate_limit_banner = f"LLM rate limit reached. Next retry automatically scheduled at {retry_time.strftime('%H:%M:%S')} UTC."
                settings_obj.rate_limit_until = retry_time
                session.add(settings_obj)
                
                job = await session.get(ArticleJob, job_id)
                if job:
                    job.status = JobStatus.queued
                    job.queue_position = 1
                    session.add(job)
                await session.commit()
            logger.warning(f"Job {job_id} hit LLM rate limit in Phase 2. Scheduled to retry in {exc.retry_after_seconds}s at {retry_time} UTC.")
            return

        logger.error(f"Job {job_id} failed in Phase 2 (Generation) stage '{job.current_step if job else 'unknown'}': {exc}", exc_info=True)
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if job:
                await _save(
                    job_id,
                    status=JobStatus.failed,
                    error_message=str(exc),
                    error_step=job.current_step,
                )
        raise


# ── Publish ───────────────────────────────────────────────────────────────────

async def publish_job(job_id: str) -> None:
    """Publish approved job to WordPress (live) + LinkedIn + Brevo.
    On republish: WordPress post is updated in-place; the existing LinkedIn
    post is deleted and re-created (LinkedIn API does not support edits).
    """
    logger.info(f"Starting publication phase for Job ID {job_id}...")
    from src.integrations.wordpress import get_client as wp_client
    from src.integrations.linkedin import get_client as li_client
    from src.integrations.brevo import get_client as brevo_client

    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job or job.status != JobStatus.approved:
            logger.warning(f"Aborting publication: Job {job_id} not found or status is not 'approved'. Status: {job.status if job else 'None'}")
            return

        settings_obj = await session.get(CompanySettings, 1)

        plan_data = job.content_plan or {}
        title = job.reviewed_title or plan_data.get("chosen_title", job.topic)
        body_md = job.reviewed_markdown or job.article_markdown or ""
        li_text = job.reviewed_linkedin or job.linkedin_post or ""
        tags = plan_data.get("secondary_keywords", [])
        focus_kw = plan_data.get("focus_keyword", "")
        meta_desc = plan_data.get("meta_description", "")
        publish_wordpress = job.publish_wordpress
        publish_linkedin = job.publish_linkedin
        publish_newsletter = job.publish_newsletter
        wp_post_id = job.wp_post_id
        linkedin_post_id = job.linkedin_post_id  # existing post to replace on republish
        selected_image = job.selected_image

        # Author info from settings
        author_id = settings_obj.wp_author_id if settings_obj else None
        author_name = (settings_obj.wp_author_name or "") if settings_obj else ""

        # Newsletter details
        newsletter_list_ids = job.newsletter_list_ids
        newsletter_subject = job.reviewed_newsletter_subject or job.newsletter_subject
        newsletter_preheader = job.reviewed_newsletter_preheader or job.newsletter_preheader
        newsletter_html = job.reviewed_newsletter_html or job.newsletter_html

    await _save(job_id, status=JobStatus.publishing)

    # Load selected image bytes
    image_bytes = None
    featured_media_id = None
    if selected_image:
        import os
        filepath = os.path.join("src/ui", selected_image.lstrip("/"))
        if os.path.exists(filepath):
            try:
                with open(filepath, "rb") as f:
                    image_bytes = f.read()
                logger.info(f"Loaded selected image from {filepath} ({len(image_bytes)} bytes)")
            except Exception as img_read_err:
                logger.error(f"Failed to read selected image file: {img_read_err}")

    try:
        # Check if the content is already HTML to bypass markdown parser
        body_stripped = body_md.strip()
        if body_stripped.startswith("<") or "</h2>" in body_stripped or "</p>" in body_stripped or "</td>" in body_stripped:
            html_body = body_md
        else:
            html_body = md.markdown(
                body_md,
                extensions=["tables", "fenced_code", "nl2br"],
            )

        wp_result = {"url": ""}
        # 1. Post to WordPress.com as draft or update existing post in-place
        if publish_wordpress:
            wp = wp_client(db_settings=settings_obj)
            
            # Upload to WordPress media library if image_bytes is loaded
            if image_bytes:
                try:
                    filename = os.path.basename(filepath)
                    logger.info(f"Job {job_id}: Uploading selected image to WordPress media library...")
                    wp_media = await wp.upload_media(filename, image_bytes)
                    featured_media_id = wp_media.get("id")
                    logger.info(f"Job {job_id}: Featured media uploaded to WordPress. ID: {featured_media_id}")
                except Exception as wp_img_err:
                    logger.error(f"Job {job_id}: Failed to upload featured image to WordPress: {wp_img_err}")

            # Retrieve WordPress categories and automatically assign the most accurate one
            category_ids = []
            try:
                categories = await wp.get_categories()
                if categories:
                    category_options = "\n".join([f"- {c['id']}: {c['name']} (slug: {c['slug']})" for c in categories])
                    cat_prompt = f"""\
You are an expert content taxonomy editor. Given the article details, choose the single most relevant/accurate category from the list of existing WordPress categories to assign to this post.

## Article Details
- Topic: {title}
- Focus Keyword: {focus_kw}

## Available WordPress Categories
{category_options}

## Task
Select the ID of the single best matching category. 
If none of them match well, select the default category ID (usually 1 or Uncategorized).
Return ONLY the chosen numeric category ID as an integer. Do not write any other text or explanation.
"""
                    from src.pipeline.llm import call_llm
                    chosen_id_str, _ = await call_llm(prompt=cat_prompt, tier="haiku")
                    import re
                    match = re.search(r'\d+', chosen_id_str)
                    if match:
                        chosen_id = int(match.group(0))
                        if any(c["id"] == chosen_id for c in categories):
                            category_ids = [chosen_id]
                            logger.info(f"Job {job_id}: Automatically matched WordPress category ID: {chosen_id}")
            except Exception as e:
                logger.error(f"Job {job_id}: Failed to dynamically select WordPress category: {e}")

            yoast_plugin_enabled = settings_obj.yoast_plugin if settings_obj else False
            yoast_seo_title = None
            if yoast_plugin_enabled:
                site_title = get_site_title(settings_obj.wp_site_url if settings_obj else None)
                if len(title) + 3 + len(site_title) > 60:
                    logger.info(f"Job {job_id}: Title is longer than 60 characters with site title. Generating shorter punchy version...")
                    yoast_seo_title = await generate_punchy_seo_title(title, focus_kw, site_title, settings_obj)
                    logger.info(f"Job {job_id}: Generated shorter SEO title: '{yoast_seo_title}'")

            if wp_post_id:
                logger.info(f"Job {job_id}: WordPress post already exists (ID: {wp_post_id}). Updating in-place...")
                wp_result = await wp.update_post(
                    post_id=wp_post_id,
                    title=title,
                    html_content=html_body,
                    focus_keyword=focus_kw,
                    meta_description=meta_desc,
                    tags=tags,
                    category_ids=category_ids,
                    author_id=author_id,
                    author_name=author_name,
                    featured_media_id=featured_media_id,
                    yoast_plugin_enabled=yoast_plugin_enabled,
                    yoast_seo_title=yoast_seo_title,
                )
                logger.info(f"Job {job_id}: WordPress post {wp_post_id} updated successfully at: {wp_result['url']}")
            else:
                logger.info(f"Job {job_id}: Publishing article to WordPress.com...")
                wp_result = await wp.create_post(
                    title=title,
                    html_content=html_body,
                    focus_keyword=focus_kw,
                    meta_description=meta_desc,
                    tags=tags,
                    category_ids=category_ids,
                    author_id=author_id,
                    author_name=author_name,
                    featured_media_id=featured_media_id,
                    yoast_plugin_enabled=yoast_plugin_enabled,
                    yoast_seo_title=yoast_seo_title,
                )
                await _save(
                    job_id,
                    wp_post_id=wp_result["post_id"],
                    wp_post_url=wp_result["url"],
                )
                logger.info(f"Job {job_id}: WordPress post published successfully at: {wp_result['url']}")

        # 2. Post to LinkedIn (delete old post first if republishing)
        if publish_linkedin:
            li = li_client(db_settings=settings_obj)
            author_urn = (settings_obj.li_person_urn if settings_obj else None)

            # If a previous LinkedIn post exists (republish), delete it first.
            # LinkedIn's API does not support editing existing posts.
            if linkedin_post_id:
                logger.info(f"Job {job_id}: Deleting previous LinkedIn post (ID: {linkedin_post_id}) before re-posting...")
                try:
                    deleted = await li.delete_post(linkedin_post_id)
                    if deleted:
                        logger.info(f"Job {job_id}: Previous LinkedIn post deleted successfully.")
                    else:
                        logger.warning(f"Job {job_id}: Previous LinkedIn post {linkedin_post_id} not found (already deleted?). Continuing.")
                except Exception as li_del_err:
                    logger.warning(f"Job {job_id}: Could not delete previous LinkedIn post: {li_del_err}. Continuing with new post.")

            logger.info(f"Job {job_id}: Posting content to LinkedIn...")
            li_result = await li.post_article(
                post_text=li_text,
                author_urn=author_urn,
                image_bytes=image_bytes,
            )
            post_urn = li_result.get("post_id", "")
            await _save(
                job_id,
                linkedin_post_id=post_urn,
            )
            logger.info(f"Job {job_id}: LinkedIn post shared successfully. Post ID: {post_urn}")

            # Post the article link in the comment section of the newly created post
            article_url = wp_result.get("url", "")
            if article_url:
                logger.info(f"Job {job_id}: Posting article URL to LinkedIn comments: {article_url}...")
                try:
                    await li.create_comment(
                        post_urn=post_urn,
                        comment_text=article_url,
                        author_urn=author_urn,
                    )
                    logger.info(f"Job {job_id}: LinkedIn comment posted successfully.")
                except Exception as comment_err:
                    logger.error(f"Job {job_id}: Failed to post link comment to LinkedIn: {comment_err}")

        # 3. Post to Brevo Newsletter
        if publish_newsletter:
            logger.info(f"Job {job_id}: Creating Brevo email newsletter campaign...")
            brevo = brevo_client(db_settings=settings_obj)
            list_ids = newsletter_list_ids
            if not list_ids and settings_obj.brevo_list_id:
                list_ids = [settings_obj.brevo_list_id]

            if not list_ids:
                raise ValueError("No Brevo List IDs configured.")

            subject = newsletter_subject
            preheader = newsletter_preheader
            html = newsletter_html

            nl_result = await brevo.create_and_send_campaign(
                name=f"Newsletter - {job_id[:8]} - {datetime.utcnow().strftime('%Y%m%d%H%M')}",
                subject=subject,
                preheader=preheader,
                html_content=html,
                list_ids=list_ids
            )
            await _save(
                job_id,
                newsletter_campaign_id=nl_result.get("campaign_id"),
            )
            logger.info(f"Job {job_id}: Brevo campaign created and scheduled. Campaign ID: {nl_result.get('campaign_id')}")

        # 4. Request Indexing on Google Search Console
        if wp_result.get("url") and settings_obj and settings_obj.gsc_service_account_json:
            logger.info(f"Job {job_id}: Submitting published article URL to Google Search Console Indexing API...")
            try:
                from src.integrations.google import GoogleSearchConsoleClient
                gsc = GoogleSearchConsoleClient(settings_obj.gsc_service_account_json)
                gsc_res = await gsc.submit_indexing(wp_result["url"])
                if gsc_res.get("ok"):
                    logger.info(f"Job {job_id}: GSC Indexing request successfully sent for {wp_result['url']}.")
                else:
                    logger.warning(f"Job {job_id}: GSC Indexing request failed: {gsc_res.get('error')}")
            except Exception as gsc_err:
                logger.warning(f"Job {job_id}: Failed to submit URL to Google Search Console: {gsc_err}")

        # 5. Post Update to Google Business Profile
        if wp_result.get("url") and settings_obj and settings_obj.gbp_access_token and settings_obj.gbp_location_id:
            logger.info(f"Job {job_id}: Generating Google Business Profile post adaptation...")
            try:
                from src.pipeline.gbp_adapt import run_gbp_adaptation
                from src.integrations.google import GoogleBusinessProfileClient
                
                target_audience = plan_data.get("target_audience", "General")
                gbp_post, gbp_usage = await run_gbp_adaptation(
                    title=title,
                    target_audience=target_audience,
                    article_markdown=body_md
                )
                
                logger.info(f"Job {job_id}: Posting adapted update to Google Business Profile locations...")
                gbp_client = GoogleBusinessProfileClient(
                    client_id=settings_obj.gbp_client_id,
                    client_secret=settings_obj.gbp_client_secret,
                    refresh_token=settings_obj.gbp_access_token,
                    account_id=settings_obj.gbp_account_id,
                    location_id=settings_obj.gbp_location_id
                )
                
                gbp_res = await gbp_client.create_local_post(
                    summary=gbp_post.summary,
                    learn_more_url=wp_result["url"]
                )
                
                if gbp_res.get("ok"):
                    post_name = gbp_res.get("post_name")
                    await _save(job_id, gbp_post_name=post_name)
                    logger.info(f"Job {job_id}: Google Business Profile post created successfully: {post_name}.")
                else:
                    logger.warning(f"Job {job_id}: Google Business Profile post failed: {gbp_res.get('error')}")
            except Exception as gbp_err:
                logger.warning(f"Job {job_id}: Failed to publish Google Business Profile update: {gbp_err}")

        await _save(
            job_id,
            status=JobStatus.published,
            current_step=None,
        )
        logger.info(f"Job {job_id}: Publication finished successfully! All targets processed.")

    except Exception as exc:
        logger.error(f"Job {job_id} failed during publication: {exc}", exc_info=True)
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if job:
                await _save(
                    job_id,
                    status=JobStatus.failed,
                    error_message=str(exc),
                    error_step="publishing" if not job.current_step else job.current_step,
                )
        raise
