import asyncio
import logging
import zoneinfo
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, ClusterPlan, JobStatus
from src.models.settings import CompanySettings
from src.pipeline.orchestrator import publish_job, run_pipeline

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Hardcoded inter-job pacing: 10 minutes between pipeline starts
INTER_JOB_DELAY = timedelta(minutes=10)


def is_in_time_window(start_hour: int | None, end_hour: int | None, tz_name: str = "Europe/London") -> bool:
    if start_hour is None or end_hour is None:
        return True
    try:
        tz = zoneinfo.ZoneInfo(tz_name or "Europe/London")
    except Exception:
        tz = zoneinfo.ZoneInfo("Europe/London")
    now = datetime.now(tz)
    current_hour = now.hour
    if start_hour <= end_hour:
        return start_hour <= current_hour < end_hour
    else:
        return current_hour >= start_hour or current_hour < end_hour


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
      0. Stale watchdog: any job stuck in 'running' for >20 min gets reset to 'queued'.
      1. If any job is currently running or resuming → do nothing (pipeline busy).
      2. If the most recently completed/failed job finished < 10 min ago → do nothing (pacing).
      3. Otherwise, pick the oldest queued/pending job and start it.
      4. Recalculate queue_position for all remaining queued jobs.
    """
    STALE_THRESHOLD = timedelta(minutes=20)
    try:
        async with AsyncSessionLocal() as session:
            # Rule 0: check for LLM rate limit pauses
            settings_obj = await session.get(CompanySettings, 1)
            if settings_obj and settings_obj.rate_limit_until:
                if settings_obj.rate_limit_until > datetime.utcnow():
                    logger.info(f"Queue paused: active rate limit until {settings_obj.rate_limit_until} UTC.")
                    return
                else:
                    # Rate limit expired! Clear the banner and block timestamp
                    settings_obj.rate_limit_until = None
                    settings_obj.rate_limit_banner = None
                    session.add(settings_obj)
                    await session.commit()
                    logger.info("Claude rate limit reset window passed. Cleared active banner and resumed queue.")

            # Rule 0.5: Check time window restriction for queue processing
            if settings_obj and settings_obj.queue_start_hour is not None and settings_obj.queue_end_hour is not None:
                if not is_in_time_window(settings_obj.queue_start_hour, settings_obj.queue_end_hour, settings_obj.queue_timezone):
                    logger.info(
                        f"Queue processing paused: current time is outside the allowed scheduling window "
                        f"({settings_obj.queue_start_hour}:00 to {settings_obj.queue_end_hour}:00 {settings_obj.queue_timezone})."
                    )
                    return

            # ── Stale-running watchdog ────────────────────────────────────────
            # If a job has been stuck in 'running' with no progress for >20 min,
            # reset it to 'queued' so the queue unblocks and it retries.
            stale_cutoff = datetime.utcnow() - STALE_THRESHOLD
            stale_stmt = select(ArticleJob).where(
                ArticleJob.status.in_([JobStatus.running, JobStatus.resuming]),
                ArticleJob.updated_at < stale_cutoff
            )
            stale_jobs = (await session.exec(stale_stmt)).all()
            for stale in stale_jobs:
                logger.warning(
                    f"Stale job detected: {stale.id} has been '{stale.status.value}' "
                    f"since {stale.updated_at} (step: {stale.current_step}). Resetting to queued."
                )
                stale.status = JobStatus.queued
                stale.current_step = None
                stale.error_message = (
                    f"Auto-reset: job was stuck in '{stale.status.value}' "
                    f"for >{int(STALE_THRESHOLD.total_seconds()//60)} minutes with no progress."
                )
                stale.updated_at = datetime.utcnow()
                session.add(stale)
            if stale_jobs:
                await session.commit()
                logger.info(f"Reset {len(stale_jobs)} stale running job(s) to queued.")

            # Rule 1: bail if pipeline is still active after watchdog
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


async def check_cluster_plans():
    """
    Retry any ClusterPlan stuck in 'generating_clusters' with step 'strategy_generation'.
    This handles transient LLM timeouts from Stage 2 — resets happen automatically.
    """
    try:
        from src.pipeline.cluster_orchestrator import run_cluster_plan_stage2
        async with AsyncSessionLocal() as session:
            stmt = select(ClusterPlan).where(
                ClusterPlan.status == "generating_clusters",
                ClusterPlan.current_step == "strategy_generation"
            )
            stalled = (await session.exec(stmt)).all()

        for plan in stalled:
            logger.info(f"Re-triggering Stage 2 for stalled cluster plan {plan.id} (LLM timeout retry)")
            asyncio.create_task(run_cluster_plan_stage2(plan.id))

    except Exception as e:
        logger.error(f"Error in check_cluster_plans: {e}")


# Schedule checks
scheduler.add_job(check_scheduled_jobs, "interval", minutes=1)
scheduler.add_job(check_pending_jobs, "interval", seconds=30)
scheduler.add_job(check_cluster_plans, "interval", minutes=2)


def start_scheduler():
    asyncio.create_task(assign_queue_positions())
    scheduler.start()
    logger.info("Scheduler started (inter-job pacing: 10 minutes).")


def stop_scheduler():
    scheduler.shutdown()
    logger.info("Scheduler stopped.")
