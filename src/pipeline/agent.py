import json
import logging
from datetime import datetime
from typing import List, Optional
import asyncio

from google import genai
from google.genai import types

from src.models.job import ArticleJob, JobStatus
from src.config import settings
from sqlmodel import select

logger = logging.getLogger(__name__)

# Initialize GenAI Client
client = genai.Client(api_key=settings.gemini_api_key)


def _make_agent_session():
    """Create a fully isolated async engine+SQLModel session for agent tools."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session = sessionmaker(engine, class_=SQLModelAsyncSession, expire_on_commit=False)
    return engine, Session


def tool_create_jobs(
    topics: List[str], 
    scheduled_dates: Optional[List[str]] = None, 
    publish_targets: List[str] = ["wordpress", "linkedin"],
    newsletter_type: str = "update",
    newsletter_timeframe: Optional[str] = None,
    newsletter_list_ids: Optional[List[int]] = None
) -> str:
    """
    Creates multiple article generation jobs from a list of topics.

    Args:
        topics: A list of topics to write articles about.
        scheduled_dates: A list of ISO-8601 datetime strings corresponding to each topic,
                         indicating when it should be scheduled. Pass an empty list for
                         immediate execution.
        publish_targets: List of where to publish. Can include 'wordpress', 'linkedin', 'newsletter'.
        newsletter_type: If target includes 'newsletter', use 'update' or 'summary'.
        newsletter_timeframe: If 'summary' newsletter, use 'week' or 'month'.
        newsletter_list_ids: If target includes 'newsletter', list of Brevo contact list IDs.
    """
    logger.info(f"Agent creating jobs for topics: {topics} targets: {publish_targets}")

    async def _run():
        local_engine, LocalSession = _make_agent_session()
        created_jobs = []
        try:
            async with LocalSession() as session:
                for i, topic in enumerate(topics):
                    schedule_dt = None
                    if scheduled_dates and i < len(scheduled_dates):
                        try:
                            schedule_dt = datetime.fromisoformat(scheduled_dates[i])
                        except ValueError:
                            pass

                    job = ArticleJob(
                        topic=topic,
                        scheduled_at=schedule_dt,
                        publish_targets=publish_targets,
                        publish_wordpress="wordpress" in publish_targets,
                        publish_linkedin="linkedin" in publish_targets,
                        publish_newsletter="newsletter" in publish_targets,
                        newsletter_type=newsletter_type,
                        newsletter_timeframe=newsletter_timeframe,
                        newsletter_list_ids=newsletter_list_ids or [],
                    )
                    session.add(job)
                    created_jobs.append(job)

                await session.commit()
                for job in created_jobs:
                    await session.refresh(job)
        finally:
            await local_engine.dispose()
        return [j.id for j in created_jobs]

    job_ids = asyncio.run(_run())

    return json.dumps({
        "status": "success",
        "message": f"Created {len(job_ids)} jobs. They will begin processing shortly.",
        "job_ids": job_ids,
    })


def tool_list_jobs(status_filter: str) -> str:
    """
    Lists jobs from the content pipeline, optionally filtered by status.

    Args:
        status_filter: Filter by job status. Use one of: 'all', 'pending', 'running',
                       'pending_review', 'approved', 'scheduled', 'published', 'failed'.
    """
    async def _run():
        local_engine, LocalSession = _make_agent_session()
        try:
            async with LocalSession() as session:
                stmt = select(ArticleJob).order_by(ArticleJob.created_at.desc())
                all_jobs = (await session.exec(stmt)).all()

                results = []
                for j in all_jobs:
                    if status_filter != 'all' and j.status.value != status_filter:
                        continue
                    results.append({
                        "id": j.id,
                        "topic": j.topic,
                        "status": j.status.value,
                        "targets": j.publish_targets,
                        "scheduled_at": j.scheduled_at.isoformat() if j.scheduled_at else None,
                        "created_at": j.created_at.isoformat()
                    })
                return results
        finally:
            await local_engine.dispose()

    results = asyncio.run(_run())
    return json.dumps(results)


def tool_edit_job(
    job_id: str, 
    new_scheduled_at: Optional[str] = None, 
    new_publish_targets: Optional[List[str]] = None,
    new_newsletter_list_ids: Optional[List[int]] = None
) -> str:
    """
    Edits an existing job's schedule, publishing targets, or newsletter lists.

    Args:
        job_id: The ID of the job to edit.
        new_scheduled_at: (Optional) New ISO-8601 datetime string.
        new_publish_targets: (Optional) New list of targets: 'wordpress', 'linkedin', 'newsletter'.
        new_newsletter_list_ids: (Optional) New list of Brevo contact list IDs.
    """
    async def _run():
        local_engine, LocalSession = _make_agent_session()
        try:
            async with LocalSession() as session:
                job = await session.get(ArticleJob, job_id)
                if not job: return {"error": "Job not found"}

                if new_scheduled_at:
                    job.scheduled_at = datetime.fromisoformat(new_scheduled_at)
                if new_publish_targets is not None:
                    job.publish_targets = new_publish_targets
                    job.publish_wordpress = "wordpress" in new_publish_targets
                    job.publish_linkedin = "linkedin" in new_publish_targets
                    job.publish_newsletter = "newsletter" in new_publish_targets
                
                if new_newsletter_list_ids is not None:
                    job.newsletter_list_ids = new_newsletter_list_ids

                session.add(job)
                await session.commit()
                return {"status": "success"}
        finally:
            await local_engine.dispose()

    result = asyncio.run(_run())
    return json.dumps(result)


def tool_delete_job(job_id: str) -> str:
    """Deletes a job from the pipeline."""
    async def _run():
        local_engine, LocalSession = _make_agent_session()
        try:
            async with LocalSession() as session:
                job = await session.get(ArticleJob, job_id)
                if not job: return {"error": "Job not found"}
                await session.delete(job)
                await session.commit()
                return {"status": "success"}
        finally:
            await local_engine.dispose()

    result = asyncio.run(_run())
    return json.dumps(result)


def get_agent_chat(history: List[dict] = []):
    """Initializes the Gemini agent with tools."""
    # Convert history dicts to types.Content
    contents = []
    for h in history:
        contents.append(types.Content(role=h["role"], parts=[types.Part.from_text(text=h["content"])]))

    return client.chats.create(
        model=settings.gemini_planning_model,
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="tool_create_jobs",
                            description="Creates multiple article generation jobs from a list of topics.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "topics": types.Schema(type="ARRAY", items=types.Schema(type="STRING")),
                                    "scheduled_dates": types.Schema(type="ARRAY", items=types.Schema(type="STRING")),
                                    "publish_targets": types.Schema(type="ARRAY", items=types.Schema(type="STRING")),
                                    "newsletter_type": types.Schema(type="STRING"),
                                    "newsletter_timeframe": types.Schema(type="STRING"),
                                },
                                required=["topics", "scheduled_dates"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_list_jobs",
                            description="Lists jobs from the content pipeline, optionally filtered by status.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "status_filter": types.Schema(type="STRING")
                                },
                                required=["status_filter"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_edit_job",
                            description="Edits an existing job's schedule or publishing targets.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "job_id": types.Schema(type="STRING"),
                                    "new_scheduled_at": types.Schema(type="STRING"),
                                    "new_publish_targets": types.Schema(type="ARRAY", items=types.Schema(type="STRING"))
                                },
                                required=["job_id"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_delete_job",
                            description="Deletes a job from the pipeline.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "job_id": types.Schema(type="STRING")
                                },
                                required=["job_id"]
                            )
                        )
                    ]
                )
            ],
            system_instruction="You are the Content Engine Agent. You manage jobs in the content pipeline. You can create, list, edit, and delete jobs. Use the provided tools. Support publishing to 'wordpress', 'linkedin', and 'newsletter' (Brevo). Newsletters can be 'update' or 'summary' (week/month)."
        ),
        history=contents
    )
