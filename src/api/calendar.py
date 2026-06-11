from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.database import get_session
from src.models.job import ArticleJob

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")

Session = Annotated[AsyncSession, Depends(get_session)]

@router.get("/calendar", response_class=HTMLResponse)
async def calendar_view(request: Request):
    return templates.TemplateResponse("calendar.html", {"request": request})

@router.get("/api/calendar/events")
async def get_calendar_events(session: Session, start: str = None, end: str = None):
    # 'start' and 'end' are passed by FullCalendar as ISO8601 strings
    stmt = select(ArticleJob).where(ArticleJob.scheduled_at != None)
    
    # Optionally filter by start/end dates if needed, but for simplicity we return all scheduled jobs
    jobs = (await session.exec(stmt)).all()
    
    events = []
    for job in jobs:
        base_title = job.reviewed_title or (job.content_plan.get("chosen_title") if job.content_plan else job.topic)
        
        # Color code based on status for the main article
        color = "#3788d8" # default blue
        if job.status.value == "published":
            color = "#28a745" # green
        elif job.status.value == "scheduled":
            color = "#ffc107" # yellow
        elif job.status.value in ["failed", "rejected"]:
            color = "#dc3545" # red
            
        # Add the main article event (or standalone newsletter)
        is_standalone_nl = getattr(job, "is_newsletter", False)
        main_title = f"📧 Newsletter: {base_title}" if is_standalone_nl else f"🌐 Article: {base_title}"
        main_color = "#6f42c1" if is_standalone_nl else color
        
        events.append({
            "id": job.id, # Base ID for drag-n-drop
            "title": main_title,
            "start": job.scheduled_at.isoformat(),
            "url": f"/jobs/{job.id}",
            "backgroundColor": main_color,
            "borderColor": main_color
        })
        
        # If it's not a standalone newsletter, add the sub-deliverables
        if not is_standalone_nl:
            if getattr(job, "publish_linkedin", False):
                events.append({
                    "id": f"{job.id}_li", # Unique ID to prevent FullCalendar merging
                    "title": f"💼 LinkedIn: {base_title}",
                    "start": job.scheduled_at.isoformat(),
                    "url": f"/jobs/{job.id}",
                    "backgroundColor": "#0077b5", # LinkedIn Blue
                    "borderColor": "#0077b5"
                })
                
            if getattr(job, "publish_newsletter", False):
                events.append({
                    "id": f"{job.id}_nl", # Unique ID to prevent FullCalendar merging
                    "title": f"📧 Newsletter: {base_title}",
                    "start": job.scheduled_at.isoformat(),
                    "url": f"/jobs/{job.id}",
                    "backgroundColor": "#6f42c1", # Purple
                    "borderColor": "#6f42c1"
                })
        
    return JSONResponse(content=events)
