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
        title = job.reviewed_title or (job.content_plan.get("chosen_title") if job.content_plan else job.topic)
        
        # Color code based on status
        color = "#3788d8" # default blue
        if job.status.value == "published":
            color = "#28a745" # green
        elif job.status.value == "scheduled":
            color = "#ffc107" # yellow
        elif job.status.value in ["failed", "rejected"]:
            color = "#dc3545" # red
            
        events.append({
            "id": job.id,
            "title": title,
            "start": job.scheduled_at.isoformat(),
            "url": f"/jobs/{job.id}",
            "backgroundColor": color,
            "borderColor": color
        })
        
    return JSONResponse(content=events)
