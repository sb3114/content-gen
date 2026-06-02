import json
import logging
from datetime import datetime
from typing import List, Optional
import asyncio

from google import genai
from google.genai import types

from src.models.job import ArticleJob, JobStatus, ClusterPlan
from src.config import settings
from sqlmodel import select

logger = logging.getLogger(__name__)

# Initialize GenAI Client
client = genai.Client(api_key=settings.gemini_api_key)

_90_DAY_STRATEGY_PROMPT = """\
You are an elite SEO and Content Strategist.
Create a Rolling 90-Day Hub & Spoke Content Strategy for the seed topic: "{seed_topic}"

Define exactly {num_pillars} core messaging pillars. 
For each core pillar, define:
- Exactly 1 high-level Hub/Cornerstone article topic (e.g. a comprehensive master guide).
- Exactly {spokes_per_pillar} highly relevant and specific Spoke article topics that link back to the cornerstone topic.

This must result in exactly {total_tasks} planned article tasks (i.e. {num_pillars} pillars * (1 Hub + {spokes_per_pillar} Spokes) = {total_tasks} articles).

For each of the {total_tasks} planned article tasks, generate a task block matching this exact structure:
- core_messaging_pillar: The name of the thematic anchor (e.g., "Hardware & Automation").
- primary_keyword: A high-volume focus keyword target for the piece.
- secondary_keywords: A list of exactly 5 targeted long-tail variations and semantically relevant phrases.
- evaluation_metrics: A dictionary containing:
  - search_volume: A realistic, simulated/estimated high search volume number (e.g., between 500 and 10000).
  - keyword_difficulty: A realistic, simulated/estimated low-to-medium keyword difficulty score (between 10 and 35) to align with our Golden Ratio threshold.
  - people_also_ask: A list of exactly 3 highly relevant questions that users frequently search for in Google ('People Also Ask') for this topic.

CRITICAL RULES:
1. DO NOT include any conversational explanations, thoughts, reasoning, or rationale for why a keyword was selected. The system must only retain numeric evaluation metrics and structured data.
2. Ensure the primary and secondary keywords are highly relevant, actionable, and intent-driven.
3. Return your final answer strictly in JSON format matching the schema below:
{{
  "seed": "{seed_topic}",
  "tasks": [
    {{
      "core_messaging_pillar": "Pillar name...",
      "primary_keyword": "focus keyword...",
      "secondary_keywords": ["keyword 1", "keyword 2", "keyword 3", "keyword 4", "keyword 5"],
      "evaluation_metrics": {{
        "search_volume": 1200,
        "keyword_difficulty": 25,
        "people_also_ask": ["question 1", "question 2", "question 3"]
      }}
    }},
    ...
  ]
}}
"""


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
    scheduled_dates: List[str] = None, 
    publish_targets: List[str] = None,
    newsletter_type: str = "update",
    newsletter_timeframe: str = "",
    newsletter_list_ids: List[int] = None
) -> str:
    """
    Creates multiple article generation jobs from a list of topics.

    Args:
        topics: A list of topics to write articles about.
        scheduled_dates: A list of ISO-8601 datetime strings corresponding to each topic.
        publish_targets: List of where to publish. Can include 'wordpress', 'linkedin', 'newsletter'.
        newsletter_type: If target includes 'newsletter', use 'update' or 'summary'.
        newsletter_timeframe: If 'summary' newsletter, use 'week' or 'month'.
        newsletter_list_ids: If target includes 'newsletter', list of Brevo contact list IDs.
    """
    if scheduled_dates is None:
        scheduled_dates = []
    if publish_targets is None:
        publish_targets = ["wordpress", "linkedin"]
    if newsletter_list_ids is None:
        newsletter_list_ids = []
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
    new_scheduled_at: str = "", 
    new_publish_targets: List[str] = [],
    new_newsletter_list_ids: List[int] = []
) -> str:
    """
    Edits an existing job's schedule, publishing targets, or newsletter lists.

    Args:
        job_id: The ID of the job to edit.
        new_scheduled_at: New ISO-8601 datetime string.
        new_publish_targets: New list of targets: 'wordpress', 'linkedin', 'newsletter'.
        new_newsletter_list_ids: New list of Brevo contact list IDs.
    """
    async def _run():
        local_engine, LocalSession = _make_agent_session()
        try:
            async with LocalSession() as session:
                job = await session.get(ArticleJob, job_id)
                if not job: return {"error": "Job not found"}

                if new_scheduled_at:
                    job.scheduled_at = datetime.fromisoformat(new_scheduled_at)
                if new_publish_targets:
                    job.publish_targets = new_publish_targets
                    job.publish_wordpress = "wordpress" in new_publish_targets
                    job.publish_linkedin = "linkedin" in new_publish_targets
                    job.publish_newsletter = "newsletter" in new_publish_targets
                
                if new_newsletter_list_ids:
                    job.newsletter_list_ids = new_newsletter_list_ids

                session.add(job)
                await session.commit()
                return {"status": "success"}
        finally:
            await local_engine.dispose()

    result = asyncio.run(_run())
    return json.dumps(result)


def tool_delete_job(job_id: str) -> str:
    """
    Deletes a job from the pipeline.

    Args:
        job_id: The ID of the job to delete.
    """
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


def tool_generate_90_day_plan(
    seed_topic: str,
    num_pillars: int = 3,
    spokes_per_pillar: int = 3
) -> str:
    """
    Generates a Rolling 90-Day Hub & Spoke content plan (structural clusters)
    based on a short seed topic.

    Args:
        seed_topic: A short seed topic (e.g. 'senior fitness tracking devices') to plan around.
        num_pillars: The number of core messaging pillars (Hubs) to plan. Default is 3.
        spokes_per_pillar: The number of article topics (Spokes) under each pillar. Default is 3.
    """
    # Dynamically read and align count with the actual number of pillars configured in CompanySettings via LLM
    def get_db_pillars_count():
        async def _run():
            local_engine, LocalSession = _make_agent_session()
            try:
                async with LocalSession() as session:
                    from src.models.settings import CompanySettings
                    settings_obj = await session.get(CompanySettings, 1)
                    if settings_obj and settings_obj.core_pillars:
                        from src.pipeline.llm import call_llm
                        prompt = f"""\
Identify and count the exact number of distinct brand/content pillars defined in the text below.

=== CONTENT PILLARS ===
{settings_obj.core_pillars}
=======================

Return a strict JSON response with a single key "num_pillars" containing the integer count of pillars:
{{
  "num_pillars": 9
}}
"""
                        text, _ = await call_llm(
                            prompt=prompt,
                            tier="haiku",
                            use_json=True,
                            db_settings=settings_obj
                        )
                        data = json.loads(text)
                        return int(data.get("num_pillars", 3))
            except Exception as e:
                logger.warning(f"Error checking brand pillars in DB via LLM: {e}")
            finally:
                await local_engine.dispose()
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _run())
                return future.result()
        else:
            return asyncio.run(_run())

    db_pillars = get_db_pillars_count()
    if db_pillars is not None:
        logger.info(f"Dynamically aligned plan with {db_pillars} database strategy pillars (overriding requested {num_pillars}).")
        num_pillars = db_pillars

    logger.info(f"Agent generating 90-day plan for seed: {seed_topic} with {num_pillars} pillars and {spokes_per_pillar} spokes each")

    if spokes_per_pillar < 3:
        return json.dumps({
            "error": f"Each core pillar requires at least 3 spokes in addition to the 1 cornerstone/hub article. Please request at least 3 spokes per pillar."
        })

    total_tasks = num_pillars * (1 + spokes_per_pillar)

    if total_tasks > 90:
        return json.dumps({
            "message": f"### ⚠️ Plan Exceeds Capacity\nIt is not possible to fit this strategy within the 90-day rolling content calendar.\n\n- **Requested Pillars**: {num_pillars}\n- **Requested Spokes per Pillar**: {spokes_per_pillar}\n- **Total Articles Required**: **{total_tasks}** ({num_pillars} Pillars × (1 Hub + {spokes_per_pillar} Spokes))\n- **Calendar Limit**: **90 days / 90 articles** (maximum rate of 1 article per day)\n\n**Please reduce either the number of pillars or the number of spokes per pillar to fit within the 90-day maximum capacity.**",
            "error": "Not enough days in the 90-day content calendar."
        })

    async def _run():
        local_engine, LocalSession = _make_agent_session()
        try:
            prompt = _90_DAY_STRATEGY_PROMPT.format(
                seed_topic=seed_topic,
                num_pillars=num_pillars,
                spokes_per_pillar=spokes_per_pillar,
                total_tasks=total_tasks
            )
            response = client.models.generate_content(
                model=settings.gemini_planning_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            plan_data = json.loads(response.text)

            async with LocalSession() as session:
                db_plan = ClusterPlan(
                    seed=seed_topic,
                    tasks=plan_data.get("tasks", []),
                    approved=False
                )
                session.add(db_plan)
                await session.commit()
                await session.refresh(db_plan)

                tasks = plan_data.get("tasks", [])
                message = f"### 📅 Generated Rolling 90-Day Hub & Spoke Content Strategy\n"
                message += f"**Seed Topic**: `{seed_topic}`\n"
                message += f"**Database ID**: `{db_plan.id}`\n\n"

                pillars = {}
                for t in tasks:
                    p = t.get("core_messaging_pillar", "General")
                    if p not in pillars:
                        pillars[p] = []
                    pillars[p].append(t)

                for pillar, pts in pillars.items():
                    message += f"#### 🎯 {pillar}\n"
                    for pt in pts:
                        metrics = pt.get("evaluation_metrics", {})
                        sv = metrics.get("search_volume", "N/A")
                        kd = metrics.get("keyword_difficulty", "N/A")
                        paa = ", ".join(metrics.get("people_also_ask", []))

                        message += f"- **Focus Keyword**: `{pt.get('primary_keyword')}`\n"
                        message += f"  - *Secondary KWs*: {', '.join(pt.get('secondary_keywords', []))}\n"
                        message += f"  - *Metrics*: SV: **{sv}** | KD: **{kd}**\n"
                        message += f"  - *People Also Ask*: {paa}\n"
                    message += "\n"

                message += f"\nTo approve and schedule all of these jobs, click one of the options below:\n"
                message += f"- **[Approve & Schedule (WordPress & LinkedIn)](/approve_clusters?id={db_plan.id}&targets=wordpress,linkedin)**\n"
                message += f"- **[Approve & Schedule (WordPress Only)](/approve_clusters?id={db_plan.id}&targets=wordpress)**\n\n"
                message += f"Or say: 'approve the clusters' (WordPress + LinkedIn) or 'approve the clusters without linkedin'."
                return {"message": message, "id": db_plan.id}
        except Exception as e:
            logger.error(f"Error in tool_generate_90_day_plan: {e}")
            return {"error": str(e)}
        finally:
            await local_engine.dispose()

    result = asyncio.run(_run())
    return json.dumps(result)


def tool_approve_and_schedule_latest_plan(publish_targets: List[str] = ["wordpress", "linkedin"]) -> str:
    """
    Approves the latest pending 90-Day Hub & Spoke cluster plan and schedules
    the writing jobs across the calendar matrix sequentially.

    Args:
        publish_targets: List of targets to publish to. Can contain 'wordpress', 'linkedin'.
                         Defaults to ['wordpress', 'linkedin']. To omit linkedin, pass ['wordpress'].
    """
    logger.info(f"Agent approving and scheduling latest cluster plan with targets: {publish_targets}")

    async def _run():
        local_engine, LocalSession = _make_agent_session()
        try:
            from src.pipeline.scheduling import schedule_writing_jobs
            async with LocalSession() as session:
                # Find latest unapproved cluster plan
                stmt = select(ClusterPlan).where(ClusterPlan.approved == False).order_by(ClusterPlan.created_at.desc())
                res = await session.exec(stmt)
                plan = res.first()
                if not plan:
                    return {"error": "No unapproved 90-Day cluster plans found."}

                # Schedule jobs
                job_ids = await schedule_writing_jobs(
                    session,
                    plan.tasks,
                    publish_targets=publish_targets,
                    cluster_plan_id=plan.id,
                    competitor_url=plan.competitor_url,
                )

                # Mark plan as approved
                plan.approved = True
                session.add(plan)
                await session.commit()

                return {
                    "status": "success",
                    "message": f"Successfully approved cluster plan '{plan.seed}' and scheduled {len(job_ids)} writing jobs across the calendar matrix sequentially with targets {publish_targets}.",
                    "job_ids": job_ids
                }
        except Exception as e:
            logger.error(f"Error in tool_approve_and_schedule_latest_plan: {e}")
            return {"error": str(e)}
        finally:
            await local_engine.dispose()

    result = asyncio.run(_run())
    return json.dumps(result)


def get_agent_chat(history: List[dict] = []):
    """Initializes the Gemini agent with tools."""
    from src.models.settings import CompanySettings

    # Fetch brand settings context
    async def _fetch_brand_context():
        local_engine, LocalSession = _make_agent_session()
        try:
            async with LocalSession() as session:
                settings_obj = await session.get(CompanySettings, 1)
                if settings_obj:
                    return {
                        "company_description": settings_obj.company_description or "",
                        "marketing_strategy": settings_obj.marketing_strategy or "",
                        "tone_of_voice": settings_obj.tone_of_voice or "",
                        "icp": settings_obj.icp or "",
                        "core_pillars": settings_obj.core_pillars or "",
                        "audiences": settings_obj.audiences or "",
                    }
        except Exception as e:
            logger.warning(f"Could not load brand voice/strategy context for agent: {e}")
        finally:
            await local_engine.dispose()
        return {}

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Running event loop exists, run task in a separate thread to avoid loop conflicts
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _fetch_brand_context())
            brand_ctx = future.result()
    else:
        brand_ctx = asyncio.run(_fetch_brand_context())

    sys_instr = (
        "You are the Content Engine Agent. You manage jobs in the content pipeline. "
        "You can create, list, edit, delete, generate 90-day plans/clusters, and approve plans. Use the provided tools. "
        "Support publishing to 'wordpress', 'linkedin', and 'newsletter' (Brevo).\n\n"
        "Here is the established Brand Voice, Target Audiences, ICP, Strategy, and Core Pillars to anchor your alignment:\n"
    )
    if brand_ctx.get("company_description"):
        sys_instr += f"- **Company Description**: {brand_ctx['company_description']}\n"
    if brand_ctx.get("marketing_strategy"):
        sys_instr += f"- **Marketing Strategy**: {brand_ctx['marketing_strategy']}\n"
    if brand_ctx.get("tone_of_voice"):
        sys_instr += f"- **Tone of Voice (Brand Voice)**: {brand_ctx['tone_of_voice']}\n"
    if brand_ctx.get("icp"):
        sys_instr += f"- **Ideal Customer Profile (ICP)**: {brand_ctx['icp']}\n"
    if brand_ctx.get("core_pillars"):
        sys_instr += f"- **Core Pillars / Focus Topics**: {brand_ctx['core_pillars']}\n"
    if brand_ctx.get("audiences"):
        sys_instr += f"- **Target Audiences**: {brand_ctx['audiences']}\n"

    sys_instr += (
        "\nUse this brand context to inform the style, voice, vocabulary, and topic generation. "
        "When generating 90-day content strategies, plans, or individual jobs, prioritize aligning with the listed "
        "Core Pillars and target audiences."
    )

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
                                    "newsletter_list_ids": types.Schema(type="ARRAY", items=types.Schema(type="INTEGER")),
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
                            description="Edits an existing job's schedule, publishing targets, or newsletter lists.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "job_id": types.Schema(type="STRING"),
                                    "new_scheduled_at": types.Schema(type="STRING"),
                                    "new_publish_targets": types.Schema(type="ARRAY", items=types.Schema(type="STRING")),
                                    "new_newsletter_list_ids": types.Schema(type="ARRAY", items=types.Schema(type="INTEGER")),
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
                        ),
                        types.FunctionDeclaration(
                            name="tool_generate_90_day_plan",
                            description="Generates a Rolling 90-Day Hub & Spoke content plan (structural clusters) based on a short seed topic.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "seed_topic": types.Schema(type="STRING"),
                                    "num_pillars": types.Schema(
                                        type="INTEGER",
                                        description="The number of core messaging pillars (Hubs) to plan. Default is 3."
                                    ),
                                    "spokes_per_pillar": types.Schema(
                                        type="INTEGER",
                                        description="The number of article topics (Spokes) under each pillar. Default is 3."
                                    )
                                },
                                required=["seed_topic"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_approve_and_schedule_latest_plan",
                            description="Approves the latest pending 90-Day Hub & Spoke cluster plan and schedules the writing jobs across the calendar matrix.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "publish_targets": types.Schema(
                                        type="ARRAY",
                                        items=types.Schema(type="STRING"),
                                        description="List of publish targets, e.g. ['wordpress', 'linkedin']. Default is ['wordpress', 'linkedin']."
                                    )
                                }
                            )
                        )
                    ]
                )
            ],
            system_instruction=sys_instr
        ),
        history=contents
    )
