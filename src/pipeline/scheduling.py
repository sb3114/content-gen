from datetime import datetime, timedelta, time
from typing import List
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


async def schedule_writing_jobs(
    session,
    tasks: List[dict],
    publish_targets: List[str] = ["wordpress", "linkedin"],
    cluster_plan_id: str = None,
    competitor_url: str = None,
) -> List[str]:
    """
    Schedules writing jobs from structural cluster task blocks.
    Each task block in tasks must have:
      - core_messaging_pillar: The high-level thematic anchor.
      - primary_keyword: The main keyword.
      - secondary_keywords: List[str].
      - evaluation_metrics: dict with search_volume, keyword_difficulty, and people_also_ask.
    """
    created_job_ids = []
    
    # Calculate scheduling pacing interval dynamically to try to fit all within 90 days.
    num_tasks = len(tasks)
    if num_tasks > 45:
        interval_days = 1
    else:
        interval_days = 2
    
    # Let's get the base slot first
    now = datetime.utcnow()
    start_dt = datetime.combine(now.date() + timedelta(days=1), time(9, 0))
    
    # Fetch scheduled times of all future jobs
    stmt = select(ArticleJob.scheduled_at).where(
        ArticleJob.scheduled_at != None,
        ArticleJob.scheduled_at >= datetime.utcnow(),
        ArticleJob.status.in_([JobStatus.scheduled, JobStatus.published, JobStatus.queued])
    )
    res = await session.exec(stmt)
    scheduled_times = res.all()
    
    if scheduled_times:
        sorted_times = sorted(scheduled_times)
        latest_time = sorted_times[-1]
        last_dt = datetime.combine(latest_time.date() + timedelta(days=1), time(9, 0))
        if last_dt < start_dt:
            last_dt = start_dt
    else:
        last_dt = start_dt
        
    for task in tasks:
        # Check input properties
        core_messaging_pillar = task.get("core_messaging_pillar")
        primary_keyword = task.get("primary_keyword")
        secondary_keywords = task.get("secondary_keywords", [])
        evaluation_metrics = task.get("evaluation_metrics", {})
        
        # Calculate next queue position
        stmt_queued = select(ArticleJob).where(
            ArticleJob.status.in_([JobStatus.queued, JobStatus.pending])
        )
        res_queued = await session.exec(stmt_queued)
        queued_count = len(res_queued.all())
        queue_pos = queued_count + 1
        
        # Format the topic cleanly
        topic = primary_keyword or "Cluster Job"
        
        # Create ArticleJob
        job = ArticleJob(
            topic=topic,
            core_messaging_pillar=core_messaging_pillar,
            primary_keyword=primary_keyword,
            secondary_keywords=secondary_keywords,
            evaluation_metrics=evaluation_metrics,
            scheduled_at=last_dt,
            status=JobStatus.queued,
            queue_position=queue_pos,
            publish_targets=publish_targets,
            publish_wordpress="wordpress" in publish_targets,
            publish_linkedin="linkedin" in publish_targets,
            publish_newsletter="newsletter" in publish_targets,
            cluster_plan_id=cluster_plan_id,
            competitor_urls=[competitor_url] if competitor_url else [],
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        created_job_ids.append(job.id)
        
        # Advance the schedule by dynamic interval for the next task block
        last_dt = last_dt + timedelta(days=interval_days)
        
    return created_job_ids
