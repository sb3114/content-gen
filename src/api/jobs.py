"""
Jobs API: create, list, get, approve, reject, publish.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession  # needs .exec()

from src.database import get_session
from src.models.job import ArticleJob, JobStatus
from src.models.settings import CompanySettings
from src.pipeline.orchestrator import publish_job, run_pipeline
from src.pipeline.refinement import run_refinement

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
    company_description: str = Form(None),
):
    settings_obj = await session.get(CompanySettings, 1)
    if not settings_obj:
        settings_obj = CompanySettings(id=1)
    
    settings_obj.marketing_strategy = marketing_strategy
    settings_obj.icp = icp
    settings_obj.core_pillars = core_pillars
    settings_obj.tone_of_voice = tone_of_voice
    settings_obj.audiences = audiences
    settings_obj.company_description = company_description
    
    from src.pipeline.summarize import summarize_company_context
    summary = await summarize_company_context(settings_obj.model_dump())
    settings_obj.summarized_context = summary
    
    session.add(settings_obj)
    await session.commit()
    
    # Redirect back to settings with success (could add flash message)
    return RedirectResponse(url="/settings", status_code=303)



# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: Session):
    jobs = (await session.exec(
        select(ArticleJob).order_by(ArticleJob.created_at.desc()).limit(50)
    )).all()
    return templates.TemplateResponse(
        "index.html", {"request": request, "jobs": jobs}
    )


# ── Create job ────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_job_form(request: Request):
    return templates.TemplateResponse("new_job.html", {"request": request})


@router.post("/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    session: Session,
    topic: str = Form(...),
    user_titles: str = Form(""),
    competitor_urls: str = Form(""),
    seed_keywords: str = Form(""),
    publish_targets: list[str] = Form(default=["wordpress", "linkedin"]),
    scheduled_at_str: str = Form(None, alias="scheduled_at"),
):
    def parse_lines(text: str) -> list[str]:
        return [l.strip() for l in text.strip().splitlines() if l.strip()]

    schedule_dt = None
    if scheduled_at_str:
        try:
            schedule_dt = datetime.fromisoformat(scheduled_at_str)
        except ValueError:
            pass

    job = ArticleJob(
        topic=topic,
        user_titles=parse_lines(user_titles),
        competitor_urls=parse_lines(competitor_urls),
        seed_keywords=parse_lines(seed_keywords),
        publish_targets=publish_targets,
        publish_wordpress="wordpress" in publish_targets,
        publish_linkedin="linkedin" in publish_targets,
        scheduled_at=schedule_dt,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    background_tasks.add_task(run_pipeline, job.id)
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


# ── Job detail / review ───────────────────────────────────────────────────────

@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str, session: Session):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return templates.TemplateResponse(
        "review.html", {"request": request, "job": job}
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
    reviewed_linkedin: str = Form(...),
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    job.reviewed_title = reviewed_title
    job.reviewed_markdown = reviewed_markdown
    job.reviewed_linkedin = reviewed_linkedin
    job.status = JobStatus.approved
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()

    background_tasks.add_task(publish_job, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Refine ────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/refine")
async def refine_job(
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


# ── Retry failed jobs ─────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/retry")
async def retry_job(
    background_tasks: BackgroundTasks, session: Session, job_id: str
):
    job = await session.get(ArticleJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = JobStatus.pending
    job.error_message = None
    job.error_step = None
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    background_tasks.add_task(run_pipeline, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
