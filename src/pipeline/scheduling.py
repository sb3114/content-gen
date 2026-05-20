from datetime import datetime, timedelta, time
from sqlmodel import select

from src.models.job import ArticleJob, JobStatus


async def get_next_open_slot(session) -> datetime:
    """
    Calculate the next sequential open calendar slot.
    Picks 9:00 AM of the day after the latest scheduled job,
    or tomorrow at 9:00 AM if no future jobs are scheduled.
    """
    # 1. Fetch scheduled times of all future scheduled/published jobs
    stmt = select(ArticleJob.scheduled_at).where(
        ArticleJob.scheduled_at != None,
        ArticleJob.scheduled_at >= datetime.utcnow(),
        ArticleJob.status.in_([JobStatus.scheduled, JobStatus.published])
    )
    res = await session.exec(stmt)
    scheduled_times = res.all()
    
    # 2. Determine base start datetime (tomorrow at 9:00 AM UTC)
    now = datetime.utcnow()
    start_dt = datetime.combine(now.date() + timedelta(days=1), time(9, 0))
    
    if not scheduled_times:
        return start_dt
        
    # Sort future scheduled times
    sorted_times = sorted(scheduled_times)
    latest_time = sorted_times[-1]
    
    # Next sequential slot is the day after the latest scheduled time at 9:00 AM
    next_slot = datetime.combine(latest_time.date() + timedelta(days=1), time(9, 0))
    
    # Fallback to tomorrow if next_slot is somehow in the past
    if next_slot < start_dt:
        return start_dt
        
    return next_slot
