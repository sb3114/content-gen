"""
Jobs API: create, list, get, approve, reject, publish.
"""
import logging
from datetime import datetime
from typing import Annotated, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.database import get_session
from src.models.job import ArticleJob, JobStatus, ClusterPlan
from src.models.settings import CompanySettings
from src.pipeline.orchestrator import publish_job, run_pipeline, resume_pipeline
from src.pipeline.refinement import run_refinement
from src.pipeline.scheduling import schedule_writing_jobs
from src.pipeline.scheduler import assign_queue_positions

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")
Session = Annotated[AsyncSession, Depends(get_session)]

# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request, session: Session):
    settings_obj = await session.get(CompanySettings, 1)
    if not settings_obj:
        settings_obj = CompanySettings(id=1)
    return templates.TemplateResponse(
        "settings.html", {"request": request, "settings": settings_obj}
    )

@router.post("/settings")
async def save_settings(
    request: Request,
    session: Session,
    marketing_strategy: str = Form(None),
    icp: str = Form(None),
    core_pillars: str = Form(None),
    tone_of_voice: str = Form(None),
    audiences: str = Form(None),
    target_audience: str = Form(None),
    personas: str = Form(None),
    pain_points: str = Form(None),
    messaging_framework: str = Form(None),
    company_description: str = Form(None),
    llm_provider: str = Form("gemini"),
    claude_setup_token: str = Form(None),
    gemini_api_key: str = Form(None),
    claude_api_key: str = Form(None),
    gemini_only_image_generation: bool = Form(False),
    yoast_plugin: bool = Form(False),
    allow_fallback_to_haiku: bool = Form(False),
    wp_site_url: str = Form(None),
    wp_username: str = Form(None),
    wp_app_password: str = Form(None),
    wp_author_id: int = Form(None),
    wp_author_name: str = Form(None),
    li_client_id: str = Form(None),
    li_client_secret: str = Form(None),
    li_access_token: str = Form(None),
    li_person_urn: str = Form(None),
    brevo_api_key: str = Form(None),
    brevo_list_id: int = Form(None),
    brevo_sender_email: str = Form(None),
    brevo_sender_name: str = Form(None),
    dataforseo_login: str = Form(None),
    dataforseo_password: str = Form(None),
    gsc_service_account_json: str = Form(None),
    gbp_access_token: str = Form(None),
    gbp_account_id: str = Form(None),
    gbp_location_id: str = Form(None),
    gbp_client_id: str = Form(None),
    gbp_client_secret: str = Form(None),
    queue_start_hour: Optional[int] = Form(None),
    queue_end_hour: Optional[int] = Form(None),
    queue_timezone: str = Form("Europe/London"),
):
    settings_obj = await session.get(CompanySettings, 1)
    if not settings_obj:
        settings_obj = CompanySettings(id=1)
    
    # Check if brand context changed to save tokens on summarization
    brand_changed = (
        settings_obj.marketing_strategy != marketing_strategy or
        settings_obj.icp != icp or
        settings_obj.core_pillars != core_pillars or
        settings_obj.tone_of_voice != tone_of_voice or
        settings_obj.audiences != audiences or
        settings_obj.target_audience != target_audience or
        settings_obj.personas != personas or
        settings_obj.pain_points != pain_points or
        settings_obj.messaging_framework != messaging_framework or
        settings_obj.company_description != company_description
    )

    settings_obj.marketing_strategy = marketing_strategy
    settings_obj.icp = icp
    settings_obj.core_pillars = core_pillars
    settings_obj.tone_of_voice = tone_of_voice
    settings_obj.audiences = audiences
    settings_obj.target_audience = target_audience
    settings_obj.personas = personas
    settings_obj.pain_points = pain_points
    settings_obj.messaging_framework = messaging_framework
    settings_obj.company_description = company_description

    # Update LLM Settings
    settings_obj.llm_provider = llm_provider
    if claude_setup_token is not None: settings_obj.claude_setup_token = claude_setup_token.strip() or None
    if gemini_api_key is not None: settings_obj.gemini_api_key = gemini_api_key.strip() or None
    if claude_api_key is not None: settings_obj.claude_api_key = claude_api_key.strip() or None
    settings_obj.gemini_only_image_generation = gemini_only_image_generation
    settings_obj.yoast_plugin = yoast_plugin
    settings_obj.allow_fallback_to_haiku = allow_fallback_to_haiku

    # Update Credentials
    if wp_site_url: settings_obj.wp_site_url = wp_site_url
    if wp_username: settings_obj.wp_username = wp_username
    if wp_app_password: settings_obj.wp_app_password = wp_app_password
    if wp_author_id is not None: settings_obj.wp_author_id = wp_author_id
    if wp_author_name: settings_obj.wp_author_name = wp_author_name
    
    if li_client_id: settings_obj.li_client_id = li_client_id
    if li_client_secret: settings_obj.li_client_secret = li_client_secret
    if li_access_token: settings_obj.li_access_token = li_access_token
    if li_person_urn: settings_obj.li_person_urn = li_person_urn
    
    if brevo_api_key: settings_obj.brevo_api_key = brevo_api_key
    if brevo_list_id is not None: settings_obj.brevo_list_id = brevo_list_id
    if brevo_sender_email: settings_obj.brevo_sender_email = brevo_sender_email
    if brevo_sender_name: settings_obj.brevo_sender_name = brevo_sender_name
    
    if dataforseo_login is not None: settings_obj.dataforseo_login = dataforseo_login
    if dataforseo_password is not None: settings_obj.dataforseo_password = dataforseo_password

    if gsc_service_account_json is not None: settings_obj.gsc_service_account_json = gsc_service_account_json
    if gbp_access_token is not None: settings_obj.gbp_access_token = gbp_access_token
    if gbp_account_id is not None: settings_obj.gbp_account_id = gbp_account_id
    if gbp_location_id is not None: settings_obj.gbp_location_id = gbp_location_id
    if gbp_client_id is not None: settings_obj.gbp_client_id = gbp_client_id
    if gbp_client_secret is not None: settings_obj.gbp_client_secret = gbp_client_secret

    settings_obj.queue_start_hour = queue_start_hour
    settings_obj.queue_end_hour = queue_end_hour
    if queue_timezone: settings_obj.queue_timezone = queue_timezone
    
    if brand_changed or not settings_obj.summarized_context:
        from src.pipeline.summarize import summarize_company_context
        # Optimized prompt: Pass only the raw fields, summarize handles the rest
        summary = await summarize_company_context(settings_obj.model_dump())
        settings_obj.summarized_context = summary
    
    session.add(settings_obj)
    await session.commit()

    # Update persistent brand context memory cache file
    from src.pipeline.memory import save_brand_context_memory
    save_brand_context_memory(settings_obj)
    
    # Redirect back to settings with success (could add flash message)
    return RedirectResponse(url="/settings", status_code=303)




# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: Session):
    settings_obj = await session.get(CompanySettings, 1)
    if not settings_obj:
        settings_obj = CompanySettings(id=1)
    
    # Standalone jobs are fetched directly at DB level to avoid limit issues
    
    plans = (await session.exec(
        select(ClusterPlan).order_by(ClusterPlan.created_at.desc()).limit(20)
    )).all()
    
    # Map child jobs to each plan for the "Job summary" view
    plan_jobs_map = {}
    for plan in plans:
        plan_jobs = (await session.exec(
            select(ArticleJob)
            .where(ArticleJob.cluster_plan_id == plan.id)
            .order_by(ArticleJob.scheduled_at.asc())
        )).all()
        plan_jobs_map[plan.id] = plan_jobs

    # Only show truly standalone jobs (no cluster association) in the main card grid
    standalone_jobs = (await session.exec(
        select(ArticleJob)
        .where(ArticleJob.cluster_plan_id == None)  # noqa: E711
        .order_by(ArticleJob.created_at.desc())
        .limit(50)
    )).all()

    return templates.TemplateResponse(
        "index.html", {
            "request": request,
            "jobs": standalone_jobs,
            "plans": plans,
            "plan_jobs_map": plan_jobs_map,
            "settings": settings_obj
        }
    )


# ── Create job ────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_job_form(request: Request, session: Session):
    from src.integrations.brevo import get_client as brevo_client
    from src.models.settings import CompanySettings
    
    settings_obj = await session.get(CompanySettings, 1)
    brevo_lists = []
    if settings_obj and settings_obj.brevo_api_key:
        try:
            brevo = brevo_client(db_settings=settings_obj)
            brevo_lists = await brevo.get_lists()
        except Exception:
            pass
            
    return templates.TemplateResponse("new_job.html", {"request": request, "brevo_lists": brevo_lists})


@router.post("/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    session: Session,
    topic: Optional[str] = Form(None),
    user_titles: str = Form(""),
    competitor_urls: str = Form(""),
    seed_keywords: str = Form(""),
    personalization_snippets: Optional[str] = Form(None),
    publish_targets: list[str] = Form(default=["wordpress", "linkedin"]),
    newsletter_type: str = Form("update"),
    newsletter_timeframe: Optional[str] = Form(None),
    newsletter_list_ids: list[int] = Form(default=[]),
    scheduled_at_str: str = Form(None, alias="scheduled_at"),
    auto_approve: bool = Form(False),
):
    def parse_lines(text: str) -> list[str]:
        return [l.strip() for l in text.strip().splitlines() if l.strip()]

    # Validation
    is_summary_only = (len(publish_targets) == 1 and publish_targets[0] == "newsletter" and newsletter_type == "summary")

    if not topic:
        if is_summary_only:
            topic = f"Newsletter Summary - {datetime.utcnow().strftime('%Y-%m-%d')}"
        else:
            raise HTTPException(status_code=400, detail="Topic is required for this job type.")

    schedule_dt = None
    if scheduled_at_str:
        try:
            schedule_dt = datetime.fromisoformat(scheduled_at_str)
        except ValueError:
            pass

    # Calculate queue position (number of existing queued/pending jobs + 1)
    from sqlmodel import select as sql_select
    queued_count = len((await session.exec(
        sql_select(ArticleJob).where(
            ArticleJob.status.in_([JobStatus.queued, JobStatus.pending])
        )
    )).all())
    queue_pos = queued_count + 1

    job = ArticleJob(
        topic=topic,
        user_titles=parse_lines(user_titles),
        competitor_urls=parse_lines(competitor_urls),
        seed_keywords=parse_lines(seed_keywords),
        personalization_snippets=personalization_snippets.strip() if personalization_snippets else None,
        publish_wordpress="wordpress" in publish_targets,
        publish_linkedin="linkedin" in publish_targets,
        publish_newsletter="newsletter" in publish_targets,
        newsletter_type=newsletter_type,
        newsletter_timeframe=newsletter_timeframe,
        newsletter_list_ids=newsletter_list_ids,
        scheduled_at=schedule_dt,
        auto_approve=auto_approve,
        status=JobStatus.queued,
        queue_position=queue_pos,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


# ── Job detail / review ───────────────────────────────────────────────────────

@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def review_page(request: Request, session: Session, job_id: str):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
        
    from src.integrations.brevo import get_client as brevo_client
    from src.models.settings import CompanySettings
    settings_obj = await session.get(CompanySettings, 1)
    brevo_lists = []
    if settings_obj and settings_obj.brevo_api_key:
        try:
            brevo = brevo_client(db_settings=settings_obj)
            brevo_lists = await brevo.get_lists()
        except Exception:
            pass

    return templates.TemplateResponse(
        "review.html", {"request": request, "job": job, "brevo_lists": brevo_lists}
    )


# ── Status polling (HTMX) ─────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/status", response_class=HTMLResponse)
async def job_status(request: Request, job_id: str, session: Session):
    job = await session.get(ArticleJob, job_id)
    if not job:
        return HTMLResponse("<span>Not found</span>")
    return templates.TemplateResponse(
        "partials/status_badge.html", {"request": request, "job": job}
    )


# ── Approve ───────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/approve")
async def approve_job(
    background_tasks: BackgroundTasks,
    session: Session,
    job_id: str,
    reviewed_title: str = Form(...),
    reviewed_markdown: str = Form(...),
    reviewed_linkedin: str = Form(None),
    reviewed_newsletter_subject: str = Form(None),
    reviewed_newsletter_html: str = Form(None),
    selected_image: str = Form(None),
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Style Learning Loop: Capture style preferences from manual edits asynchronously
    original_markdown = job.article_markdown or ""
    new_markdown = reviewed_markdown or ""
    if original_markdown.strip() != new_markdown.strip():
        from src.pipeline.memory import record_edit_feedback
        background_tasks.add_task(
            record_edit_feedback,
            original_markdown,
            new_markdown,
            job.topic
        )

    job.reviewed_title = reviewed_title
    job.reviewed_markdown = reviewed_markdown
    if reviewed_linkedin: job.reviewed_linkedin = reviewed_linkedin
    if reviewed_newsletter_subject: job.reviewed_newsletter_subject = reviewed_newsletter_subject
    if reviewed_newsletter_html: job.reviewed_newsletter_html = reviewed_newsletter_html
    if selected_image: job.selected_image = selected_image
    if job.scheduled_at and job.scheduled_at > datetime.utcnow():
        job.status = JobStatus.scheduled
    else:
        job.status = JobStatus.approved
    
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()

    if job.status == JobStatus.approved:
        background_tasks.add_task(publish_job, job_id)
    
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

# ── Reschedule ────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/reschedule")
async def reschedule_job(
    session: Session,
    job_id: str,
    scheduled_at_str: str = Form(..., alias="scheduled_at"),
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    try:
        schedule_dt = datetime.fromisoformat(scheduled_at_str)
        job.scheduled_at = schedule_dt
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
    except ValueError:
        raise HTTPException(400, "Invalid datetime format")
        
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Refine ────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/refine")
async def refine_job(
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session,
    job_id: str,
    user_prompt: str = Form(...),
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    current_md = job.reviewed_markdown or job.article_markdown or ""
    current_li = job.reviewed_linkedin or job.linkedin_post or ""
    
    # 1. Update history with user's message
    history = list(job.chat_history) if job.chat_history else []
    history.append({"role": "user", "content": user_prompt})
    
    # Style Learning Loop: Extract evergreen tone/style guidelines from chat prompt in background
    from src.pipeline.memory import record_style_feedback
    background_tasks.add_task(record_style_feedback, user_prompt)
    
    settings_obj = await session.get(CompanySettings, 1)
    company_context = settings_obj.summarized_context if settings_obj else ""

    # 2. Call refinement pipeline
    updated_content, refine_usage = await run_refinement(current_md, current_li, user_prompt, company_context)
    
    # 3. Update history with AI's acknowledgment
    history.append({"role": "ai", "content": "I've updated the requested content for you."})
    
    job.reviewed_markdown = updated_content.get("updated_article", current_md)
    if job.publish_linkedin:
        job.reviewed_linkedin = updated_content.get("updated_linkedin", current_li)
    job.chat_history = history
    
    # Update Token Usage
    job.input_tokens_used = (job.input_tokens_used or 0) + refine_usage["in"]
    job.output_tokens_used = (job.output_tokens_used or 0) + refine_usage["out"]
    job.updated_at = datetime.utcnow()
    
    session.add(job)
    await session.commit()
    
    # Return HTMX OOB response (updates chat list + markdown editors)
    return templates.TemplateResponse(
        "partials/chat_update.html", {"request": request, "job": job}
    )


# ── Reject ────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/reject")
async def reject_job(session: Session, job_id: str):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = JobStatus.rejected
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    return RedirectResponse(url="/", status_code=303)


# ── Cancel Review ─────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/cancel-review")
async def cancel_review_job(session: Session, job_id: str):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
        
    if job.wp_post_id or job.linkedin_post_id or job.newsletter_campaign_id or job.gbp_post_name:
        job.status = JobStatus.published
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
    else:
        raise HTTPException(400, "Job was not previously published, cannot cancel review.")


# ── Retry failed jobs ─────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/retry")
async def retry_job(
    background_tasks: BackgroundTasks, session: Session, job_id: str
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = JobStatus.queued
    job.error_message = None
    job.error_step = None
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Send back to review ───────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/re-review")
async def re_review_job(session: Session, job_id: str):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = JobStatus.pending_review
    job.updated_at = datetime.utcnow()
    
    if not job.generated_images:
        try:
            from src.pipeline.image_gen import generate_images_for_job
            from src.models.settings import CompanySettings
            settings_obj = await session.get(CompanySettings, 1)
            images = await generate_images_for_job(job, db_settings=settings_obj)
            job.generated_images = images
            if images:
                job.selected_image = images[0]
        except Exception as e:
            logger.error(f"Failed to generate images on re-review: {e}")
            
    session.add(job)
    await session.commit()
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Regenerate Images ──────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/regenerate_images")
async def regenerate_job_images(
    session: Session,
    job_id: str,
    feedback: Optional[str] = Form(None)
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
        
    from src.pipeline.image_gen import generate_images_for_job
    from src.models.settings import CompanySettings
    settings_obj = await session.get(CompanySettings, 1)
    
    try:
        images = await generate_images_for_job(job, db_settings=settings_obj, feedback=feedback)
        job.generated_images = images
        if images:
            job.selected_image = images[0]
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
    except Exception as e:
        logger.error(f"Failed to regenerate images: {e}")
        raise HTTPException(500, f"Failed to regenerate images: {str(e)}")
        
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/delete")
async def delete_job(session: Session, job_id: str):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    await session.delete(job)
    await session.commit()
    return RedirectResponse(url="/", status_code=303)


# ── Edit metadata ─────────────────────────────────────────────────────────────

async def recalculate_publish_targets(job_id: str, added_linkedin: bool, added_newsletter: bool):
    from src.database import AsyncSessionLocal
    from src.models.job import ArticleJob
    from src.models.settings import CompanySettings
    from src.schemas.content_plan import ContentPlan
    from src.pipeline.linkedin_adapt import run_linkedin_adaptation
    from src.pipeline.newsletter_adapt import run_newsletter_adaptation

    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job:
            return
        
        settings_obj = await session.get(CompanySettings, 1)
        company_context = settings_obj.summarized_context if settings_obj else ""
        plan = ContentPlan(**job.content_plan) if job.content_plan else None
        article_md = job.reviewed_markdown or job.article_markdown or ""
        
        input_tokens = job.input_tokens_used or 0
        output_tokens = job.output_tokens_used or 0

    # 1. Adapt LinkedIn
    li_post = None
    if added_linkedin:
        try:
            li_post, li_usage = await run_linkedin_adaptation(plan, article_md)
            input_tokens += li_usage["in"]
            output_tokens += li_usage["out"]
        except Exception as e:
            print(f"Error recalculating LinkedIn target: {e}")

    # 2. Adapt Newsletter
    nl_data = None
    if added_newsletter:
        try:
            nl_data, nl_usage = await run_newsletter_adaptation(job_id, plan, article_md)
            input_tokens += nl_usage["in"]
            output_tokens += nl_usage["out"]
        except Exception as e:
            print(f"Error recalculating Newsletter target: {e}")

    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if job:
            if li_post:
                job.linkedin_post = li_post.full_text
                job.reviewed_linkedin = li_post.full_text
            if nl_data:
                job.newsletter_subject = nl_data.subject
                job.newsletter_preheader = nl_data.preheader
                job.newsletter_html = nl_data.body_html
                job.reviewed_newsletter_subject = nl_data.subject
                job.reviewed_newsletter_preheader = nl_data.preheader
                job.reviewed_newsletter_html = nl_data.body_html
            
            job.input_tokens_used = input_tokens
            job.output_tokens_used = output_tokens
            job.updated_at = datetime.utcnow()
            session.add(job)
            await session.commit()


@router.post("/jobs/{job_id}/edit")
async def edit_job(
    background_tasks: BackgroundTasks,
    session: Session,
    job_id: str,
    publish_wordpress: bool = Form(False),
    publish_linkedin: bool = Form(False),
    publish_newsletter: bool = Form(False),
    newsletter_list_ids: list[int] = Form(default=[]),
    scheduled_at: str = Form("", alias="scheduled_at"),
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        return RedirectResponse(url="/", status_code=303)

    # Detect target changes
    added_linkedin = publish_linkedin and not job.publish_linkedin
    added_newsletter = publish_newsletter and not job.publish_newsletter

    removed_linkedin = not publish_linkedin and job.publish_linkedin
    removed_newsletter = not publish_newsletter and job.publish_newsletter

    job.publish_wordpress = publish_wordpress
    job.publish_linkedin = publish_linkedin
    job.publish_newsletter = publish_newsletter
    job.newsletter_list_ids = newsletter_list_ids

    targets = []
    if publish_wordpress:
        targets.append("wordpress")
    if publish_linkedin:
        targets.append("linkedin")
    if publish_newsletter:
        targets.append("newsletter")
    job.publish_targets = targets

    if scheduled_at:
        try:
            job.scheduled_at = datetime.fromisoformat(scheduled_at)
        except ValueError:
            pass
    else:
        job.scheduled_at = None

    # Clear removed targets content
    if removed_linkedin:
        job.linkedin_post = None
        job.reviewed_linkedin = None
    if removed_newsletter:
        job.newsletter_subject = None
        job.newsletter_preheader = None
        job.newsletter_html = None
        job.reviewed_newsletter_subject = None
        job.reviewed_newsletter_preheader = None
        job.reviewed_newsletter_html = None

    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()

    if added_linkedin or added_newsletter:
        background_tasks.add_task(recalculate_publish_targets, job.id, added_linkedin, added_newsletter)

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/save_content")
async def save_job_content(
    background_tasks: BackgroundTasks,
    session: Session,
    job_id: str,
    reviewed_title: str = Form(...),
    reviewed_markdown: str = Form(...),
    reviewed_linkedin: str = Form(None),
    reviewed_newsletter_subject: str = Form(None),
    reviewed_newsletter_html: str = Form(None),
    selected_image: str = Form(None),
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Style Learning Loop: Capture style preferences from manual edits asynchronously
    original_markdown = job.article_markdown or ""
    new_markdown = reviewed_markdown or ""
    if original_markdown.strip() != new_markdown.strip():
        from src.pipeline.memory import record_edit_feedback
        background_tasks.add_task(
            record_edit_feedback,
            original_markdown,
            new_markdown,
            job.topic
        )

    job.reviewed_title = reviewed_title
    job.reviewed_markdown = reviewed_markdown
    if reviewed_linkedin: job.reviewed_linkedin = reviewed_linkedin
    if reviewed_newsletter_subject: job.reviewed_newsletter_subject = reviewed_newsletter_subject
    if reviewed_newsletter_html: job.reviewed_newsletter_html = reviewed_newsletter_html
    if selected_image: job.selected_image = selected_image

    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Keyword Confirmation Gate ─────────────────────────────────────────────────

@router.post("/jobs/{job_id}/confirm-keyword")
async def confirm_keyword(
    background_tasks: BackgroundTasks,
    session: Session,
    job_id: str,
    confirmed_keyword: str = Form(...),
):
    """User confirms (or overrides) the AI-selected keyword. Resumes pipeline Phase 2."""
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not (job.status == JobStatus.pending_review and job.current_step == "keyword_confirmation"):
        raise HTTPException(400, "Job is not awaiting keyword confirmation")

    job.confirmed_keyword = confirmed_keyword.strip()
    job.status = JobStatus.resuming
    job.current_step = None
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()

    background_tasks.add_task(resume_pipeline, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/pause")
async def pause_job(
    request: Request,
    session: Session,
    job_id: str,
):
    """Manually pause a queued, running, or resuming job."""
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status in [JobStatus.queued, JobStatus.pending, JobStatus.running, JobStatus.resuming]:
        job.status = JobStatus.paused
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()

    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/resume")
async def resume_job(
    request: Request,
    session: Session,
    job_id: str,
):
    """Resume a paused job by placing it back in the queue."""
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status == JobStatus.paused:
        job.status = JobStatus.queued
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()

    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Queue Status (HTMX polling) ───────────────────────────────────────────────

from fastapi.responses import JSONResponse

@router.get("/jobs/queue-status")
async def queue_status(session: Session):
    """Returns current queue positions for HTMX polling on the dashboard."""
    from sqlmodel import select as sql_select
    stmt = sql_select(ArticleJob).where(
        ArticleJob.status.in_([
            JobStatus.queued, JobStatus.pending, JobStatus.running, JobStatus.resuming
        ])
    ).order_by(ArticleJob.queue_position.asc().nulls_last(), ArticleJob.created_at.asc())
    jobs = (await session.exec(stmt)).all()
    return JSONResponse([
        {
            "id": j.id,
            "topic": j.topic or "",
            "status": j.status.value,
            "queue_position": j.queue_position,
        }
        for j in jobs
    ])


# ── Rolling 90-Day Content Planner Routes ──────────────────────────────────────

from src.pipeline.cluster_orchestrator import run_cluster_plan_stage1, run_cluster_plan_stage2

@router.post("/cluster-plans")
async def create_cluster_plan(
    background_tasks: BackgroundTasks,
    session: Session,
    seed_topic: Optional[str] = Form(None),
    min_search_volume: int = Form(50),
    max_search_volume: int = Form(1000),
    max_difficulty: int = Form(40),
    competitor_url: Optional[str] = Form(None),
    publish_targets: list[str] = Form(default=["wordpress", "linkedin"])
):
    """Creates a new stateful ClusterPlan and triggers Agent 1 keyword discovery."""
    final_seed = seed_topic.strip() if (seed_topic and seed_topic.strip()) else "Brand Strategy"
    plan = ClusterPlan(
        seed=final_seed,
        status="planning",
        current_step="keyword_research",
        min_search_volume=min_search_volume,
        max_search_volume=max_search_volume,
        max_difficulty=max_difficulty,
        competitor_url=competitor_url.strip() if competitor_url else None,
        publish_targets=publish_targets,
        keywords=[],
        tasks=[]
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)

    background_tasks.add_task(run_cluster_plan_stage1, plan.id)
    return RedirectResponse(url=f"/cluster-plans/{plan.id}", status_code=303)


@router.get("/cluster-plans/{plan_id}", response_class=HTMLResponse)
async def get_cluster_plan(request: Request, session: Session, plan_id: str):
    """Renders the stateful 90-Day Cluster Plan dashboard."""
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    # Calculate scheduling dates bounds if tasks are present
    start_date = None
    end_date = None
    if plan.tasks:
        try:
            dates = [datetime.fromisoformat(t["scheduled_at"]) for t in plan.tasks if t.get("scheduled_at")]
            if dates:
                start_date = min(dates).strftime("%b %d, %Y")
                end_date = max(dates).strftime("%b %d, %Y")
        except Exception:
            pass

    # Fetch all child jobs associated with this plan
    child_jobs = (await session.exec(
        select(ArticleJob)
        .where(ArticleJob.cluster_plan_id == plan_id)
        .order_by(ArticleJob.scheduled_at.asc())
    )).all()

    return templates.TemplateResponse(
        "cluster_plan.html",
        {
            "request": request,
            "plan": plan,
            "start_date": start_date,
            "end_date": end_date,
            "child_jobs": child_jobs,
        }
    )


@router.get("/cluster-plans/{plan_id}/status", response_class=HTMLResponse)
async def get_cluster_plan_status(request: Request, session: Session, plan_id: str):
    """Returns HTML partial of the timeline and keyword panel for HTMX polling."""
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
        
    # Calculate scheduling dates bounds
    start_date = None
    end_date = None
    if plan.tasks:
        try:
            dates = [datetime.fromisoformat(t["scheduled_at"]) for t in plan.tasks if t.get("scheduled_at")]
            if dates:
                start_date = min(dates).strftime("%b %d, %Y")
                end_date = max(dates).strftime("%b %d, %Y")
        except Exception:
            pass

    return templates.TemplateResponse(
        "partials/cluster_workflow_state.html",
        {
            "request": request,
            "plan": plan,
            "start_date": start_date,
            "end_date": end_date,
        }
    )


# ── HTMX Keyword Management Actions ───────────────────────────────────────────

@router.post("/cluster-plans/{plan_id}/keywords/add", response_class=HTMLResponse)
async def add_plan_keyword(
    request: Request,
    session: Session,
    plan_id: str,
    new_kw: str = Form(...),
    pillar: str = Form("General Strategy"),
    role: str = Form("spoke"),
    source: str = Form("custom")
):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    keywords = list(plan.keywords or [])
    # Check if keyword already exists
    if not any(k.get("keyword", "").lower() == new_kw.lower() for k in keywords):
        keywords.append({
            "keyword": new_kw.strip(),
            "search_volume": 100,
            "keyword_difficulty": 20,
            "secondary_keywords": [],
            "status": "approved",
            "pillar": pillar.strip(),
            "role": role.strip(),
            "source": source.strip(),
            "paa_questions": []
        })
        plan.keywords = keywords
        flag_modified(plan, "keywords")
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

    return templates.TemplateResponse(
        "partials/cluster_keywords_list.html",
        {"request": request, "plan": plan}
    )


@router.post("/cluster-plans/{plan_id}/keywords/{kw_idx}/update", response_class=HTMLResponse)
async def update_plan_keyword(
    request: Request,
    session: Session,
    plan_id: str,
    kw_idx: int,
    keyword: str = Form(...),
    volume: int = Form(100),
    difficulty: int = Form(20),
    secondary_kws: str = Form(""),
    pillar: str = Form("General Strategy"),
    role: str = Form("spoke"),
    source: str = Form("discovery")
):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    keywords = list(plan.keywords or [])
    if 0 <= kw_idx < len(keywords):
        sec_list = [s.strip() for s in secondary_kws.split(",") if s.strip()]
        keywords[kw_idx]["keyword"] = keyword.strip()
        keywords[kw_idx]["search_volume"] = volume
        keywords[kw_idx]["keyword_difficulty"] = difficulty
        keywords[kw_idx]["secondary_keywords"] = sec_list
        keywords[kw_idx]["pillar"] = pillar.strip()
        keywords[kw_idx]["role"] = role.strip()
        keywords[kw_idx]["source"] = source.strip()
        plan.keywords = keywords
        flag_modified(plan, "keywords")
        session.add(plan)
        await session.commit()
        await session.refresh(plan)


    return templates.TemplateResponse(
        "partials/cluster_keywords_list.html",
        {"request": request, "plan": plan}
    )


@router.post("/cluster-plans/{plan_id}/keywords/{kw_idx}/delete", response_class=HTMLResponse)
async def delete_plan_keyword(
    request: Request,
    session: Session,
    plan_id: str,
    kw_idx: int
):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    keywords = list(plan.keywords or [])
    if 0 <= kw_idx < len(keywords):
        keywords[kw_idx]["status"] = "deleted"
        plan.keywords = keywords
        flag_modified(plan, "keywords")
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

    return templates.TemplateResponse(
        "partials/cluster_keywords_list.html",
        {"request": request, "plan": plan}
    )


@router.post("/cluster-plans/{plan_id}/keywords/{kw_idx}/toggle", response_class=HTMLResponse)
async def toggle_plan_keyword(
    request: Request,
    session: Session,
    plan_id: str,
    kw_idx: int
):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    keywords = list(plan.keywords or [])
    if 0 <= kw_idx < len(keywords):
        current_status = keywords[kw_idx].get("status", "approved")
        keywords[kw_idx]["status"] = "pending" if current_status == "approved" else "approved"
        plan.keywords = keywords
        flag_modified(plan, "keywords")
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

    return templates.TemplateResponse(
        "partials/cluster_keywords_list.html",
        {"request": request, "plan": plan}
    )


# ── Resume Strategy Step & Approvals ──────────────────────────────────────────

@router.post("/cluster-plans/{plan_id}/confirm-keywords")
async def confirm_keywords(
    background_tasks: BackgroundTasks,
    session: Session,
    plan_id: str
):
    """Confirms the keyword list and kicks off Agent 2 strategy clustering in the background."""
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    plan.status = "generating_clusters"
    plan.current_step = "strategy_generation"
    session.add(plan)
    await session.commit()

    background_tasks.add_task(run_cluster_plan_stage2, plan.id)
    
    # We return HTMX redirect to refresh the main planning view
    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": f"/cluster-plans/{plan.id}"}
    )


@router.post("/cluster-plans/{plan_id}/update-task-schedule")
async def update_task_schedule(
    session: Session,
    plan_id: str,
    task_idx: int = Form(...),
    scheduled_at: str = Form(...)
):
    """Allows user to dynamically edit individual article scheduled dates in place."""
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    
    tasks = list(plan.tasks or [])
    if 0 <= task_idx < len(tasks):
        try:
            # Parse and format back to ISO
            parsed_dt = datetime.fromisoformat(scheduled_at.replace("Z", ""))
            tasks[task_idx]["scheduled_at"] = parsed_dt.isoformat()
            plan.tasks = tasks
            session.add(plan)
            await session.commit()
        except Exception as e:
            logger.error(f"Failed to update task schedule date: {e}")
            raise HTTPException(status_code=400, detail="Invalid date format")

    return HTMLResponse("OK")


@router.post("/cluster-plans/{plan_id}/approve")
async def approve_cluster_plan(
    session: Session,
    plan_id: str,
    targets: Optional[str] = Form(None)
):
    """
    Final approval.
    Creates ArticleJob rows sequentially with the customized dates/times and publish targets,
    setting plan status to 'approved'.
    """
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")

    publish_targets = plan.publish_targets or ["wordpress", "linkedin"]
    if targets:
        publish_targets = [t.strip() for t in targets.split(",") if t.strip()]

    # Create and schedule individual ArticleJobs
    created_jobs = []
    for idx, task in enumerate(plan.tasks):
        # Calculate queue position
        stmt_queued = select(ArticleJob).where(
            ArticleJob.status.in_([JobStatus.queued, JobStatus.pending])
        )
        res_queued = await session.exec(stmt_queued)
        queued_count = len(res_queued.all())
        queue_pos = queued_count + 1

        scheduled_dt = None
        if task.get("scheduled_at"):
            scheduled_dt = datetime.fromisoformat(task["scheduled_at"])

        # Create ArticleJob
        job = ArticleJob(
            topic=task.get("topic") or "Cluster Job",
            core_messaging_pillar=task.get("core_messaging_pillar"),
            primary_keyword=task.get("primary_keyword"),
            secondary_keywords=task.get("secondary_keywords", []),
            evaluation_metrics=task.get("evaluation_metrics", {}),
            scheduled_at=scheduled_dt,
            status=JobStatus.queued,
            queue_position=queue_pos,
            publish_targets=publish_targets,
            publish_wordpress="wordpress" in publish_targets,
            publish_linkedin="linkedin" in publish_targets,
            publish_newsletter="newsletter" in publish_targets,
            cluster_plan_id=plan.id,
            competitor_urls=[plan.competitor_url] if plan.competitor_url else [],
        )
        session.add(job)
        created_jobs.append(job)

    plan.approved = True
    plan.status = "approved"
    session.add(plan)
    await session.commit()

    logger.info(f"Approved and bulk scheduled {len(created_jobs)} articles from plan {plan_id}.")
    return RedirectResponse(url="/", status_code=303)


# Keep legacy route for backward compatibility
@router.post("/approve_clusters")
@router.get("/approve_clusters")
async def approve_clusters(
    request: Request,
    session: Session,
    id: Optional[str] = None,
    targets: Optional[str] = None,
):
    if id:
        return await approve_cluster_plan(session, id, targets)
    
    # Fallback to latest unapproved plan
    stmt = select(ClusterPlan).where(ClusterPlan.approved == False).order_by(ClusterPlan.created_at.desc())
    res = await session.exec(stmt)
    plan = res.first()
    if not plan:
        raise HTTPException(status_code=404, detail="No unapproved plans found.")
    return await approve_cluster_plan(session, plan.id, targets)


@router.post("/cluster-plans/{plan_id}/delete")
async def delete_cluster_plan(session: Session, plan_id: str):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
    await session.delete(plan)
    await session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.get("/cluster-plans/active-badge", response_class=HTMLResponse)
async def get_active_plan_badge(request: Request, session: Session):
    stmt = (
        select(ClusterPlan)
        .where(ClusterPlan.status.in_(["planning", "generating_clusters", "keyword_review", "cluster_review"]))
        .order_by(ClusterPlan.created_at.desc())
    )
    res = await session.exec(stmt)
    active_plans = res.all()
    
    if not active_plans:
        return HTMLResponse("")
        
    plan = active_plans[0]
    
    badge_html = ""
    if plan.status in ["keyword_review", "cluster_review"]:
        action_name = "Review Keywords" if plan.status == "keyword_review" else "Review Clusters"
        badge_html = f"""
        <style>
        @keyframes nav-pulse {{
          0% {{ transform: scale(0.95); opacity: 0.6; }}
          50% {{ transform: scale(1.15); opacity: 1; }}
          100% {{ transform: scale(0.95); opacity: 0.6; }}
        }}
        </style>
        <a href="/cluster-plans/{plan.id}" class="nav-link" style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); padding: 0.25rem 0.6rem; border-radius: var(--radius-sm); font-size: 0.75rem; font-weight: 600; display: inline-flex; align-items: center; gap: 0.35rem; text-decoration: none; box-shadow: 0 0 10px rgba(245, 158, 11, 0.15); transition: border-color 0.2s;">
          <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #fbbf24; animation: nav-pulse 1.5s infinite;"></span>
          ⚠️ Action Pending: {action_name}
        </a>
        """
    else:
        status_name = "Discovering Keywords" if plan.status == "planning" else "Building Strategy"
        badge_html = f"""
        <style>
        @keyframes nav-pulse {{
          0% {{ transform: scale(0.95); opacity: 0.6; }}
          50% {{ transform: scale(1.15); opacity: 1; }}
          100% {{ transform: scale(0.95); opacity: 0.6; }}
        }}
        </style>
        <a href="/cluster-plans/{plan.id}" class="nav-link" style="background: rgba(124, 58, 237, 0.15); color: var(--accent-light); border: 1px solid rgba(124, 58, 237, 0.35); padding: 0.25rem 0.6rem; border-radius: var(--radius-sm); font-size: 0.75rem; font-weight: 600; display: inline-flex; align-items: center; gap: 0.35rem; text-decoration: none; box-shadow: 0 0 10px rgba(124, 58, 237, 0.15); transition: border-color 0.2s;">
          <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); animation: nav-pulse 1.5s infinite;"></span>
          🤖 Agent Active: {status_name}
        </a>
        """
        
    return HTMLResponse(badge_html)


@router.post("/cluster-plans/{plan_id}/pause")
async def pause_cluster_plan(request: Request, session: Session, plan_id: str):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
        
    plan.status = "paused"
    session.add(plan)
    
    stmt = select(ArticleJob).where(
        ArticleJob.cluster_plan_id == plan.id,
        ArticleJob.status.in_([JobStatus.queued, JobStatus.pending])
    )
    jobs = (await session.exec(stmt)).all()
    for job in jobs:
        job.status = JobStatus.paused
        job.queue_position = None
        session.add(job)
        
    await session.commit()
    await assign_queue_positions()
    
    referer = request.headers.get("referer") or f"/cluster-plans/{plan.id}"
    return RedirectResponse(url=referer, status_code=303)


@router.post("/cluster-plans/{plan_id}/resume")
async def resume_cluster_plan(request: Request, session: Session, plan_id: str):
    plan = await session.get(ClusterPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Cluster plan not found")
        
    plan.status = "approved"
    session.add(plan)
    
    stmt = select(ArticleJob).where(
        ArticleJob.cluster_plan_id == plan.id,
        ArticleJob.status == JobStatus.paused
    )
    jobs = (await session.exec(stmt)).all()
    for job in jobs:
        job.status = JobStatus.queued
        session.add(job)
        
    await session.commit()
    await assign_queue_positions()
    
    referer = request.headers.get("referer") or f"/cluster-plans/{plan.id}"
    return RedirectResponse(url=referer, status_code=303)


