"""
Main pipeline orchestrator. Runs as a FastAPI BackgroundTask.
Updates job status at every step so the UI stays in sync.
"""
import markdown as md
from datetime import datetime
from slugify import slugify

from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus
from src.pipeline.research import run_research
from src.pipeline.planning import run_planning
from src.pipeline.writing import run_writing
from src.pipeline.linkedin_adapt import run_linkedin_adaptation
from src.schemas.content_plan import ContentPlan


async def _save(job_id: str, **kwargs):
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job:
            return
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()


async def run_pipeline(job_id: str) -> None:
    """Full pipeline: research → plan → write → linkedin → ready for review."""
    
    # 1. Fetch initial job state to get configuration
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job:
            return
        topic = job.topic
        seed_keywords = job.seed_keywords
        competitor_urls = job.competitor_urls
        user_titles = job.user_titles
        publish_linkedin = job.publish_linkedin
        current_step = job.current_step
        
        from src.models.settings import CompanySettings
        settings_obj = await session.get(CompanySettings, 1)
        company_context = settings_obj.summarized_context if settings_obj else ""

    try:
        # ── Step 1: Research ────────────────────────────────────────
        input_tokens = 0
        output_tokens = 0
        
        await _save(job_id, status=JobStatus.running, current_step="research")

        research = await run_research(
            topic=topic,
            seed_keywords=seed_keywords,
            competitor_urls=competitor_urls,
        )
        await _save(
            job_id,
            keyword_data=research["keyword_data"],
            scraped_content=research["scraped_content"],
            current_step="planning",
        )

        # ── Step 2: Content Plan ────────────────────────────────────
        plan, plan_usage = await run_planning(
            topic=topic,
            user_titles=user_titles,
            keyword_data=research["keyword_data"],
            scraped_content=research["scraped_content"],
            company_context=company_context,
        )
        input_tokens += plan_usage["in"]
        output_tokens += plan_usage["out"]
        
        await _save(
            job_id,
            content_plan=plan.model_dump(),
            reviewed_title=plan.chosen_title,
            current_step="writing",
            input_tokens_used=input_tokens,
            output_tokens_used=output_tokens,
        )

        # ── Step 3: Write Article ───────────────────────────────────
        article_md, write_usage = await run_writing(plan, company_context)
        input_tokens += write_usage["in"]
        output_tokens += write_usage["out"]
        
        await _save(
            job_id,
            article_markdown=article_md,
            reviewed_markdown=article_md,
            current_step="linkedin",
            input_tokens_used=input_tokens,
            output_tokens_used=output_tokens,
        )

        # ── Step 4: LinkedIn Adaptation ─────────────────────────────
        if publish_linkedin:
            li_post, li_usage = await run_linkedin_adaptation(plan, article_md)
            input_tokens += li_usage["in"]
            output_tokens += li_usage["out"]
            
            await _save(
                job_id,
                linkedin_post=li_post.full_text,
                reviewed_linkedin=li_post.full_text,
                status=JobStatus.pending_review,
                current_step=None,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
        else:
            await _save(
                job_id,
                status=JobStatus.pending_review,
                current_step=None,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )

    except Exception as exc:
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




async def publish_job(job_id: str) -> None:
    """Publish approved job to WordPress (draft) + LinkedIn."""
    from src.integrations.wordpress import get_client as wp_client
    from src.integrations.linkedin import get_client as li_client

    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job or job.status != JobStatus.approved:
            return
        plan_data = job.content_plan or {}
        title = job.reviewed_title or plan_data.get("chosen_title", job.topic)
        body_md = job.reviewed_markdown or job.article_markdown or ""
        li_text = job.reviewed_linkedin or job.linkedin_post or ""
        tags = plan_data.get("secondary_keywords", [])
        focus_kw = plan_data.get("focus_keyword", "")
        meta_desc = plan_data.get("meta_description", "")
        publish_wordpress = job.publish_wordpress
        publish_linkedin = job.publish_linkedin

    await _save(job_id, status=JobStatus.publishing)

    try:
        # Convert Markdown → HTML for WordPress
        html_body = md.markdown(
            body_md,
            extensions=["tables", "fenced_code", "nl2br"],
        )

        wp_result = {"url": ""}
        # Post to WordPress.com as draft
        if publish_wordpress:
            wp = wp_client()
            wp_result = await wp.create_draft(
                title=title,
                html_content=html_body,
                focus_keyword=focus_kw,
                meta_description=meta_desc,
                tags=tags,
            )
            await _save(
                job_id,
                wp_post_id=wp_result["post_id"],
                wp_post_url=wp_result["url"],
            )

        # Post to LinkedIn
        if publish_linkedin:
            li = li_client()
            from src.config import settings
            author_urn = settings.linkedin_person_urn or ""
            li_result = await li.post_article(
                post_text=li_text,
                article_url=wp_result["url"],
                author_urn=author_urn,
            )
            await _save(
                job_id,
                linkedin_post_id=li_result.get("post_id", ""),
            )
        
        await _save(
            job_id,
            status=JobStatus.published,
            current_step=None,
        )

    except Exception as exc:
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
