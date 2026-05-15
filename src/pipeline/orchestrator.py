import asyncio
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


async def _save(job_id: str, **kwargs):
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            job.updated_at = datetime.utcnow()
            session.add(job)
            await session.commit()


async def run_pipeline(job_id: str) -> None:
    """Full async pipeline: Research → Plan → Write → Adapt. Supports resuming."""
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if not job:
                return
            
            topic = job.topic
            user_titles = job.user_titles
            competitor_urls = job.competitor_urls
            seed_keywords = job.seed_keywords
            publish_linkedin = job.publish_linkedin
            publish_newsletter = job.publish_newsletter
            
            # Initialize tokens from current job state (in case of resume)
            input_tokens = job.input_tokens_used or 0
            output_tokens = job.output_tokens_used or 0
            
            # Fetch company context
            settings_obj = await session.get(CompanySettings, 1)
            company_context = settings_obj.summarized_context if settings_obj else ""
            
            # Fetch Published Memory (Cross-linking context)
            from src.pipeline.summarize import get_published_memory
            published_memory = await get_published_memory()
            full_context = company_context + "\n" + published_memory

        # ── Step 0: Decision Logic ────────────────────────────────
        is_newsletter_summary = (job.newsletter_type == "summary" and publish_newsletter)
        
        # ── Step 1: Research ──────────────────────────────────────
        if not is_newsletter_summary and (not job.keyword_data or not job.scraped_content):
            await _save(job_id, status=JobStatus.running, current_step="research")
            research_data = await run_research(
                topic, seed_keywords, competitor_urls
            )
            await _save(
                job_id,
                current_step="planning",
                keyword_data=research_data["keyword_data"],
                scraped_content=research_data["scraped_content"],
            )
            research_data_dict = research_data
        elif not is_newsletter_summary:
            research_data_dict = {
                "keyword_data": job.keyword_data,
                "scraped_content": job.scraped_content
            }
        else:
            research_data_dict = {"keyword_data": None, "scraped_content": None}

        # ── Step 1.5: Viability Check (Token Optimization) ──────
        if not is_newsletter_summary and research_data_dict["keyword_data"]:
            volumes = research_data_dict["keyword_data"].get("volumes", {})
            # Algorithm: if kd <= 30 and search_volume > 500: write_article()
            # We use (competition * 100) as a proxy for Keyword Difficulty (KD)
            is_viable = any(
                (v.get("competition", 1.0) * 100 <= 30) and (v.get("search_volume", 0) >= 500)
                for v in volumes.values()
            )
            
            if not is_viable and not job.content_plan:
                msg = "Token Optimization: No keywords met the threshold (KD <= 30 & Volume >= 500). Job stopped to save resources."
                await _save(job_id, status=JobStatus.failed, error_message=msg)
                return

        # ── Step 2: Planning ──────────────────────────────────────
        if not is_newsletter_summary and not job.content_plan:
            await _save(job_id, status=JobStatus.running, current_step="planning")
            plan, plan_usage = await run_planning(
                topic=topic,
                user_titles=user_titles,
                keyword_data=research_data_dict["keyword_data"],
                scraped_content=research_data_dict["scraped_content"],
                company_context=full_context
            )
            input_tokens += plan_usage["in"]
            output_tokens += plan_usage["out"]

            await _save(
                job_id,
                current_step="writing",
                content_plan=plan.model_dump(),
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
        elif not is_newsletter_summary:
            plan = ContentPlan(**job.content_plan)
        else:
            plan = None

        # ── Step 3: Writing ───────────────────────────────────────
        if not is_newsletter_summary and not job.article_markdown:
            await _save(job_id, status=JobStatus.running, current_step="writing")
            article_md, write_usage = await run_writing(plan, company_context=full_context)
            input_tokens += write_usage["in"]
            output_tokens += write_usage["out"]

            await _save(
                job_id,
                current_step="linkedin" if publish_linkedin else ("newsletter" if publish_newsletter else None),
                article_markdown=article_md,
                reviewed_markdown=article_md,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )
        else:
            article_md = job.article_markdown or ""


        # ── Step 4: LinkedIn Adaptation ─────────────────────────────
        if not is_newsletter_summary and publish_linkedin and not job.linkedin_post:
            await _save(job_id, status=JobStatus.running, current_step="linkedin")
            li_post, li_usage = await run_linkedin_adaptation(plan, article_md)
            input_tokens += li_usage["in"]
            output_tokens += li_usage["out"]
            
            await _save(
                job_id,
                current_step="newsletter" if publish_newsletter else None,
                linkedin_post=li_post.full_text,
                reviewed_linkedin=li_post.full_text,
                input_tokens_used=input_tokens,
                output_tokens_used=output_tokens,
            )

        # ── Step 5: Newsletter Adaptation ───────────────────────────
        if publish_newsletter and not job.newsletter_html:
            await _save(job_id, status=JobStatus.running, current_step="newsletter")
            nl_data, nl_usage = await run_newsletter_adaptation(job_id, plan, article_md)
            input_tokens += nl_usage["in"]
            output_tokens += nl_usage["out"]

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

        await _save(
            job_id,
            status=JobStatus.pending_review,
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
                    error_step=job.current_step,
                )
        raise


async def publish_job(job_id: str) -> None:
    """Publish approved job to WordPress (draft) + LinkedIn + Brevo."""
    from src.integrations.wordpress import get_client as wp_client
    from src.integrations.linkedin import get_client as li_client
    from src.integrations.brevo import get_client as brevo_client

    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job or job.status != JobStatus.approved:
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

    await _save(job_id, status=JobStatus.publishing)

    try:
        # Convert Markdown → HTML for WordPress
        html_body = md.markdown(
            body_md,
            extensions=["tables", "fenced_code", "nl2br"],
        )

        wp_result = {"url": ""}
        # 1. Post to WordPress.com as draft
        if publish_wordpress:
            wp = wp_client(db_settings=settings_obj)
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

        # 2. Post to LinkedIn
        if publish_linkedin:
            li = li_client(db_settings=settings_obj)
            # Use wp_result['url'] if available, otherwise just post the text
            author_urn = (settings_obj.li_person_urn if settings_obj else None)
            li_result = await li.post_article(
                post_text=li_text,
                article_url=wp_result.get("url", ""),
                author_urn=author_urn,
            )
            await _save(
                job_id,
                linkedin_post_id=li_result.get("post_id", ""),
            )

        # 3. Post to Brevo Newsletter
        if publish_newsletter:
            brevo = brevo_client(db_settings=settings_obj)
            list_ids = job.newsletter_list_ids
            if not list_ids and settings_obj.brevo_list_id:
                list_ids = [settings_obj.brevo_list_id]
                
            if not list_ids:
                raise ValueError("No Brevo List IDs configured.")
            
            subject = job.reviewed_newsletter_subject or job.newsletter_subject
            preheader = job.reviewed_newsletter_preheader or job.newsletter_preheader
            html = job.reviewed_newsletter_html or job.newsletter_html
            
            nl_result = await brevo.create_and_send_campaign(
                name=f"Newsletter - {job.topic[:20]} - {datetime.utcnow().strftime('%Y%m%d%H%M')}",
                subject=subject,
                preheader=preheader,
                html_content=html,
                list_ids=list_ids
            )
            await _save(
                job_id,
                newsletter_campaign_id=nl_result.get("campaign_id"),
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
