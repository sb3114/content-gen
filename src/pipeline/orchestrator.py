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
            auto_approve = job.auto_approve

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

        # ── Decision: newsletter summary bypasses keyword gate ────────────────
        is_newsletter_summary = (job.newsletter_type == "summary" and publish_newsletter)

        # ── Step 1: Research ──────────────────────────────────────────────────
        if not is_newsletter_summary and (not job.keyword_data or not job.scraped_content):
            await _save(job_id, status=JobStatus.running, current_step="research")
            research_data = await run_research(
                topic, seed_keywords, competitor_urls, db_settings=settings_obj
            )

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
                await _save(job_id, status=JobStatus.failed, error_message=msg)
                return

        # ── Keyword Gate ──────────────────────────────────────────────────────
        # auto_approve=True → skip gate, use AI-chosen keyword directly
        # auto_approve=False → pause for user confirmation (unless plan already exists)
        if not is_newsletter_summary and not job.content_plan:
            if auto_approve:
                # Set confirmed_keyword to the AI-chosen keyword and go straight to Phase 2
                kw_data = research_data_dict.get("keyword_data") or {}
                chosen = kw_data.get("chosen_keyword", {})
                auto_kw = chosen.get("keyword", "") if isinstance(chosen, dict) else ""
                await _save(job_id, confirmed_keyword=auto_kw)
                await resume_pipeline(job_id)
                return
            else:
                # Pause here — UI will show the keyword review panel
                await _save(
                    job_id,
                    status=JobStatus.pending_review,
                    current_step="keyword_confirmation",
                )
                return

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


# ── Phase 2: Write → Adapt → (Auto-)Approve ──────────────────────────────────

async def resume_pipeline(job_id: str) -> None:
    """
    Phase 2 of 2: Plan → Write → Adapt → Review or Auto-Publish.

    Called either by the confirm-keyword API endpoint (user confirmed keyword)
    or directly from run_pipeline() when auto_approve=True.
    """
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(ArticleJob, job_id)
            if not job:
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

        # Pull SERP format from keyword_review_data to inject into planning
        async with AsyncSessionLocal() as session:
            refreshed = await session.get(ArticleJob, job_id)
            kd = refreshed.keyword_data or {}
            scraped = refreshed.scraped_content or []
            krd = refreshed.keyword_review_data or {}

        serp_format = krd.get("serp_format", "") if krd else ""

        # ── Step 2: Planning ──────────────────────────────────────────────────
        if not is_newsletter_summary and not job.content_plan:
            await _save(job_id, status=JobStatus.running, current_step="planning")

            plan, plan_usage = await run_planning(
                topic=topic,
                user_titles=user_titles,
                keyword_data=kd,
                scraped_content=scraped,
                company_context=full_context,
                focus_keyword=focus_kw,
                serp_format=serp_format,
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

        # ── Step 3: Writing ───────────────────────────────────────────────────
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

        # ── Step 4: LinkedIn Adaptation ───────────────────────────────────────
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

        # ── Step 5: Newsletter Adaptation ─────────────────────────────────────
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

        # ── Gate: Auto-approve or Human Review ────────────────────────────────
        if auto_approve:
            await _save(job_id, status=JobStatus.approved, current_step=None)
            await publish_job(job_id)
        else:
            await _save(job_id, status=JobStatus.pending_review, current_step=None)

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


# ── Publish ───────────────────────────────────────────────────────────────────

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

        # Author info from settings
        author_id = settings_obj.wp_author_id if settings_obj else None
        author_name = (settings_obj.wp_author_name or "") if settings_obj else ""

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
                author_id=author_id,
                author_name=author_name,
            )
            await _save(
                job_id,
                wp_post_id=wp_result["post_id"],
                wp_post_url=wp_result["url"],
            )

        # 2. Post to LinkedIn
        if publish_linkedin:
            li = li_client(db_settings=settings_obj)
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
