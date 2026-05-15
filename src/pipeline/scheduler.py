import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus
from src.pipeline.orchestrator import publish_job, run_pipeline

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def check_scheduled_jobs():
    """Poll database for jobs that are scheduled and ready to publish."""
    try:
        async with AsyncSessionLocal() as session:
            # Find jobs that are in "scheduled" status and scheduled_at is in the past/present
            stmt = select(ArticleJob).where(
                ArticleJob.status == JobStatus.scheduled,
                ArticleJob.scheduled_at <= datetime.utcnow()
            )
            result = await session.exec(stmt)
            jobs_to_publish = result.all()

            for job in jobs_to_publish:
                logger.info(f"Triggering scheduled publish for job {job.id}")
                # Change status immediately to approved so it doesn't get picked up again
                # and then call publish_job
                job.status = JobStatus.approved
                session.add(job)
                await session.commit()
                
                # trigger the actual publish task asynchronously
                asyncio.create_task(publish_job(job.id))

    except Exception as e:
        logger.error(f"Error checking scheduled jobs: {e}")

async def check_pending_jobs():
    """Poll database for new pending jobs and kick off their pipelines."""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(ArticleJob).where(ArticleJob.status == JobStatus.pending)
            result = await session.exec(stmt)
            pending_jobs = result.all()

            for job in pending_jobs:
                logger.info(f"Triggering generation pipeline for pending job {job.id}")
                # Update status to running immediately so it isn't picked up again
                job.status = JobStatus.running
                session.add(job)
                await session.commit()
                
                # trigger the pipeline task asynchronously
                asyncio.create_task(run_pipeline(job.id))

    except Exception as e:
        logger.error(f"Error checking pending jobs: {e}")

# Run the schedule checker every minute
scheduler.add_job(check_scheduled_jobs, 'interval', minutes=1)
# Run the pending pipeline queue every 10 seconds
scheduler.add_job(check_pending_jobs, 'interval', seconds=10)

def start_scheduler():
    scheduler.start()
    logger.info("Scheduler started.")

def stop_scheduler():
    scheduler.shutdown()
    logger.info("Scheduler stopped.")
