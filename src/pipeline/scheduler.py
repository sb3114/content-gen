import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus
from src.pipeline.orchestrator import publish_job, run_pipeline

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Hardcoded inter-job pacing: 10 minutes between pipeline starts
INTER_JOB_DELAY = timedelta(minutes=10)


async def check_scheduled_jobs():
    """Poll database for jobs that are scheduled and ready to publish."""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(ArticleJob).where(
                ArticleJob.status == JobStatus.scheduled,
                ArticleJob.scheduled_at <= datetime.utcnow()
            )
            result = await session.exec(stmt)
            jobs_to_publish = result.all()

            for job in jobs_to_publish:
                logger.info(f"Triggering scheduled publish for job {job.id}")
                job.status = JobStatus.approved
                session.add(job)
                await session.commit()
                asyncio.create_task(publish_job(job.id))

    except Exception as e:
        logger.error(f"Error checking scheduled jobs: {e}")


async def check_pending_jobs():
    """
    Sequential job queue processor with 10-minute pacing.

    Rules:
      1. If any job is currently running or resuming → do nothing (pipeline busy).
      2. If the most recently completed/failed job finished < 10 min ago → do nothing (pacing).
      3. Otherwise, pick the oldest queued/pending job and start it.
      4. Recalculate queue_position for all remaining queued jobs.
    """
    try:
        async with AsyncSessionLocal() as session:
            # Rule 1: bail if pipeline is active
            active_stmt = select(ArticleJob).where(
                ArticleJob.status.in_([JobStatus.running, JobStatus.resuming])
            )
            active_jobs = (await session.exec(active_stmt)).all()
            if active_jobs:
                return

            # Rule 2: pacing — check when the last job completed/failed
            recent_stmt = select(ArticleJob).where(
                ArticleJob.status.in_([
                    JobStatus.pending_review, JobStatus.published,
                    JobStatus.failed, JobStatus.rejected,
                ])
            ).order_by(ArticleJob.updated_at.desc()).limit(1)
            recent = (await session.exec(recent_stmt)).first()
            if recent and recent.updated_at:
                elapsed = datetime.utcnow() - recent.updated_at
                if elapsed < INTER_JOB_DELAY:
                    remaining = int((INTER_JOB_DELAY - elapsed).total_seconds() / 60)
                    logger.debug(f"Pacing: next job starts in ~{remaining} min")
                    return

            # Rule 3: pick the oldest queued/pending job
            queued_stmt = select(ArticleJob).where(
                ArticleJob.status.in_([JobStatus.queued, JobStatus.pending])
            ).order_by(ArticleJob.created_at.asc())
            queued_jobs = (await session.exec(queued_stmt)).all()

            if not queued_jobs:
                return

            next_job = queued_jobs[0]
            logger.info(f"Starting queued job {next_job.id} (topic: {next_job.topic})")
            next_job.status = JobStatus.running
            next_job.queue_position = None
            session.add(next_job)

            # Rule 4: renumber remaining queued jobs
            for position, job in enumerate(queued_jobs[1:], start=1):
                job.queue_position = position
                session.add(job)

            await session.commit()
            asyncio.create_task(run_pipeline(next_job.id))

    except Exception as e:
        logger.error(f"Error checking pending jobs: {e}")


async def assign_queue_positions():
    """
    On startup (or after restart), assign queue_position to all queued/pending
    jobs that don't already have one, so the dashboard shows correct ordering.
    """
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(ArticleJob).where(
                ArticleJob.status.in_([JobStatus.queued, JobStatus.pending])
            ).order_by(ArticleJob.created_at.asc())
            jobs = (await session.exec(stmt)).all()
            for position, job in enumerate(jobs, start=1):
                job.queue_position = position
                session.add(job)
            await session.commit()
    except Exception as e:
        logger.error(f"Error assigning queue positions: {e}")


# Schedule checks
scheduler.add_job(check_scheduled_jobs, "interval", minutes=1)
scheduler.add_job(check_pending_jobs, "interval", seconds=30)


def start_scheduler():
    asyncio.create_task(assign_queue_positions())
    scheduler.start()
    logger.info("Scheduler started (inter-job pacing: 10 minutes).")


def stop_scheduler():
    scheduler.shutdown()
    logger.info("Scheduler stopped.")
