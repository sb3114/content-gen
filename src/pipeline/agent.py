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
    spokes_per_pillar: int = 3,
    audience_split: Optional[List[dict]] = None
) -> str:
    """
    Generates a Rolling 90-Day Hub & Spoke content plan (structural clusters)
    based on a short seed topic.

    Args:
        seed_topic: A short seed topic (e.g. 'senior fitness tracking devices') to plan around.
        num_pillars: The number of core messaging pillars (Hubs) to plan. Default is 3.
        spokes_per_pillar: The number of article topics (Spokes) under each pillar. Default is 3.
        audience_split: An ordered list of dicts specifying how to split content across audience personas.
            Each dict must have 'persona' (str) and 'percentage' (int, must sum to 100).
            Example: [{"persona": "CTOs", "percentage": 40}, {"persona": "Developers", "percentage": 60}].
            If provided, the orchestrator will distribute articles proportionally across these personas.
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

    # Validate audience_split if provided
    if audience_split:
        total_pct = sum(int(a.get("percentage", 0)) for a in audience_split)
        if total_pct != 100:
            return json.dumps({
                "error": f"Audience split percentages must sum to 100. Got {total_pct}%. "
                         f"Please adjust: {audience_split}"
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
                    approved=False,
                    audience_split=audience_split or None
                )
                session.add(db_plan)
                await session.commit()
                await session.refresh(db_plan)

                tasks = plan_data.get("tasks", [])
                message = f"### 📅 Generated Rolling 90-Day Hub & Spoke Content Strategy\n"
                message += f"**Seed Topic**: `{seed_topic}`\n"
                message += f"**Database ID**: `{db_plan.id}`\n"

                # Show audience split summary if provided
                if audience_split:
                    message += f"\n**🎯 Audience Split**:\n"
                    for entry in audience_split:
                        message += f"- `{entry['persona']}`: {entry['percentage']}%\n"
                message += "\n"

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
                plan.status = "approved"
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


class SettingsMock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class ClaudePart:
    def __init__(self, name, args):
        self.function_call = self
        self.name = name
        self.args = args

class ClaudeCandidate:
    def __init__(self, parts):
        self.content = self
        self.parts = parts

class ClaudeResponse:
    def __init__(self, text="", function_calls=None):
        self.text = text
        self.candidates = [ClaudeCandidate([ClaudePart(name, args) for name, args in function_calls])] if function_calls else []

class ClaudeChat:
    def __init__(self, history: List[dict], sys_instr: str, db_settings=None):
        self.history = list(history)
        self.sys_instr = sys_instr
        self.db_settings = db_settings

    def send_message(self, message):
        import json
        import asyncio
        from src.pipeline.llm import call_llm

        if isinstance(message, list):
            for part in message:
                if hasattr(part, "function_response") and part.function_response:
                    resp_dict = part.function_response.response
                    tool_name = part.function_response.name
                    self.history.append({
                        "role": "user",
                        "content": f"Tool '{tool_name}' executed. Result: {json.dumps(resp_dict)}"
                    })
        else:
            self.history.append({"role": "user", "content": str(message)})

        # Format history for Claude
        history_text = ""
        for item in self.history:
            role = "User" if item["role"] == "user" else "Assistant"
            history_text += f"\n### {role}:\n{item['content']}\n"

        prompt = f"""\
You are the Content Engine Agent. You have access to the following tools:
1. tool_create_jobs: Creates article writing jobs from a list of topics.
   Args: topics (array of strings), scheduled_dates (array of strings), publish_targets (array of strings, optional), newsletter_type (string, optional), newsletter_timeframe (string, optional), newsletter_list_ids (array of ints, optional).
2. tool_list_jobs: Lists active jobs in the pipeline.
   Args: status_filter (string).
3. tool_edit_job: Edits an existing job's scheduling or publication targets.
   Args: job_id (string), new_scheduled_at (string, optional), new_publish_targets (array of strings, optional), new_newsletter_list_ids (array of ints, optional).
4. tool_delete_job: Deletes a job from the content pipeline.
   Args: job_id (string).
5. tool_generate_90_day_plan: Generates a Hub & Spoke rolling strategy.
   Args: seed_topic (string), num_pillars (int, optional), spokes_per_pillar (int, optional),
   audience_split (array of objects, optional) — e.g. [{{"persona": "CTOs", "percentage": 40}}, {{"persona": "Developers", "percentage": 60}}].
   Percentages must sum to 100. Each article task will be tagged with a target_persona field.
6. tool_approve_and_schedule_latest_plan: Approves the latest plan and schedules all generated child jobs.
   Args: publish_targets (array of strings, optional).
7. tool_list_cluster_plans: Lists cluster plans. Args: status_filter (string, optional).
8. tool_pause_cluster_plan: Pauses a cluster plan, preventing further processing. Args: plan_id (string).
9. tool_resume_cluster_plan: Resumes a paused cluster plan. Args: plan_id (string).
10. tool_delete_cluster_plan: Deletes a cluster plan completely. Args: plan_id (string).
11. tool_modify_cluster_plan: Modifies a cluster plan properties. Args: plan_id (string), modifications_json (string).
12. tool_reschedule_cluster_plan: Reschedules all jobs in a cluster plan. Args: plan_id (string), start_date (string, YYYY-MM-DD), end_date (string, YYYY-MM-DD, optional).

## Conversation History & Latest Messages
{history_text}

## Task
Decide to either call a tool or reply to the user.
You MUST reply with a JSON object. 
- If you call a tool:
{{
  "tool_call": {{
    "name": "tool_name",
    "arguments": {{ ... }}
  }}
}}
- If you reply directly to the user:
{{
  "reply": "Your direct reply message here."
}}
"""

        async def _async_call():
            text, _ = await call_llm(
                prompt=prompt,
                tier="sonnet",
                system_instruction=self.sys_instr,
                use_json=True,
                db_settings=self.db_settings
            )
            return text

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _async_call())
                raw_text = future.result()
        else:
            raw_text = asyncio.run(_async_call())

        try:
            parsed = json.loads(raw_text)
        except Exception:
            parsed = {"reply": raw_text}

        if "tool_call" in parsed and parsed["tool_call"]:
            tool_name = parsed["tool_call"].get("name", "")
            tool_args = parsed["tool_call"].get("arguments", {})
            self.history.append({
                "role": "model",
                "content": f"Requesting tool execution for '{tool_name}' with args {json.dumps(tool_args)}"
            })
            return ClaudeResponse(function_calls=[(tool_name, tool_args)])
        else:
            reply_text = parsed.get("reply", raw_text)
            self.history.append({
                "role": "model",
                "content": reply_text
            })
            return ClaudeResponse(text=reply_text)


def get_agent_chat(history: List[dict] = []):
    """Initializes the agent chat (routing to Claude CLI if setup token is present, else Gemini)."""
    from src.pipeline.memory import load_brand_context_memory
    from src.database import AsyncSessionLocal
    from src.models.settings import CompanySettings

    brand_ctx = load_brand_context_memory()

    async def _get_claude_token():
        local_engine, LocalSession = _make_agent_session()
        try:
            async with LocalSession() as session:
                settings_obj = await session.get(CompanySettings, 1)
                if settings_obj:
                    return settings_obj.claude_setup_token, {
                        "llm_provider": settings_obj.llm_provider,
                        "claude_setup_token": settings_obj.claude_setup_token,
                        "allow_fallback_to_haiku": settings_obj.allow_fallback_to_haiku,
                    }
        except Exception as e:
            logger.warning(f"Could not load settings in get_agent_chat: {e}")
        finally:
            await local_engine.dispose()
        return None, None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _get_claude_token())
            claude_token, db_settings_dict = future.result()
    else:
        claude_token, db_settings_dict = asyncio.run(_get_claude_token())

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
    if brand_ctx.get("icp_context"):
        sys_instr += f"\n**ICP & Persona Strategy** (full context):\n{brand_ctx['icp_context']}\n"

    sys_instr += (
        "\nUse this brand context to inform the style, voice, vocabulary, and topic generation. "
        "When generating 90-day content strategies, plans, or individual jobs, prioritize aligning with the listed "
        "Core Pillars and target audiences.\n"
        "IMPORTANT DIRECTIVE: Before you trigger the content plan orchestrator (generating a 90-day plan), "
        "you MUST read the whole strategy context and actively ask the user clarifying questions. Specifically:\n"
        "  1. Which specific audience personas (from the Personas list above) should this plan target?\n"
        "  2. What is the desired content split between these personas? (e.g. 50% CTOs, 30% Developers, 20% Marketing Managers — must sum to 100%)\n"
        "  3. Are there specific pain points or messaging angles to prioritise for each persona?\n"
        "Wait for their response, then call tool_generate_90_day_plan with the audience_split parameter populated based on their answers."
    )

    if claude_token:
        # Route to Claude CLI agent chat
        db_settings = SettingsMock(**db_settings_dict) if db_settings_dict else None
        return ClaudeChat(history, sys_instr, db_settings=db_settings)

    # Fallback to standard Gemini client chat
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
                                    ),
                                    "audience_split": types.Schema(
                                        type="ARRAY",
                                        description="Optional distribution of content across audience personas. Each item must have 'persona' (string) and 'percentage' (integer). Percentages must sum to 100.",
                                        items=types.Schema(
                                            type="OBJECT",
                                            properties={
                                                "persona": types.Schema(type="STRING", description="The persona name, e.g. 'CTOs', 'Developers'."),
                                                "percentage": types.Schema(type="INTEGER", description="The percentage of articles for this persona (0-100).")
                                            },
                                            required=["persona", "percentage"]
                                        )
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
                        ),
                        types.FunctionDeclaration(
                            name="tool_list_cluster_plans",
                            description="Lists cluster plans. Optional filter by status.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "status_filter": types.Schema(type="STRING", description="Optional status filter (e.g. 'planning', 'paused').")
                                }
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_pause_cluster_plan",
                            description="Pauses a cluster plan, preventing further processing.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "plan_id": types.Schema(type="STRING", description="The ID of the plan to pause.")
                                },
                                required=["plan_id"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_resume_cluster_plan",
                            description="Resumes a paused cluster plan.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "plan_id": types.Schema(type="STRING", description="The ID of the plan to resume.")
                                },
                                required=["plan_id"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_delete_cluster_plan",
                            description="Deletes a cluster plan completely.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "plan_id": types.Schema(type="STRING", description="The ID of the plan to delete.")
                                },
                                required=["plan_id"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_modify_cluster_plan",
                            description="Modifies a cluster plan properties.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "plan_id": types.Schema(type="STRING", description="The ID of the plan to modify."),
                                    "modifications_json": types.Schema(type="STRING", description="A JSON string representing the properties to update.")
                                },
                                required=["plan_id", "modifications_json"]
                            )
                        ),
                        types.FunctionDeclaration(
                            name="tool_reschedule_cluster_plan",
                            description="Reschedules all jobs in a cluster plan to be evenly spaced.",
                            parameters=types.Schema(
                                type="OBJECT",
                                properties={
                                    "plan_id": types.Schema(type="STRING", description="The ID of the plan to reschedule."),
                                    "start_date": types.Schema(type="STRING", description="Start date in YYYY-MM-DD format."),
                                    "end_date": types.Schema(type="STRING", description="Optional end date in YYYY-MM-DD format. If provided, jobs are evenly spaced between start and end.")
                                },
                                required=["plan_id", "start_date"]
                            )
                        )
                    ]
                )
            ],
            system_instruction=sys_instr
        ),
        history=contents
    )

def tool_list_cluster_plans(status_filter: str = None) -> str:
    """Lists cluster plans. Optional filter by status."""
    engine, LocalSession = _make_agent_session()
    import asyncio
    async def _run():
        try:
            async with LocalSession() as session:
                query = select(ClusterPlan)
                if status_filter:
                    query = query.where(ClusterPlan.status == status_filter)
                plans = (await session.exec(query)).all()
                if not plans:
                    return json.dumps({"status": "success", "message": "No cluster plans found."})
                res = []
                for p in plans:
                    res.append({
                        "id": str(p.id),
                        "seed": p.seed,
                        "status": p.status,
                        "created_at": p.created_at.isoformat(),
                        "num_keywords": len(p.keywords or [])
                    })
                return json.dumps({"status": "success", "plans": res})
        finally:
            await engine.dispose()
    return asyncio.run(_run())

def tool_pause_cluster_plan(plan_id: str) -> str:
    """Pauses a cluster plan and all its associated queued article jobs, preventing further processing."""
    engine, LocalSession = _make_agent_session()
    import asyncio
    async def _run():
        try:
            async with LocalSession() as session:
                p = await session.get(ClusterPlan, plan_id)
                if not p: return json.dumps({"error": f"Plan {plan_id} not found."})
                p.status = "paused"
                session.add(p)
                
                # Cascade to article jobs
                jobs_query = select(ArticleJob).where(ArticleJob.cluster_plan_id == plan_id)
                jobs = (await session.exec(jobs_query)).all()
                paused_count = 0
                for job in jobs:
                    if job.status in (JobStatus.queued, JobStatus.pending, JobStatus.pending_review, JobStatus.resuming):
                        job.status = JobStatus.paused
                        session.add(job)
                        paused_count += 1

                await session.commit()
                return json.dumps({"status": "success", "message": f"Cluster plan {plan_id} paused along with {paused_count} associated jobs."})
        finally:
            await engine.dispose()
    return asyncio.run(_run())

def tool_resume_cluster_plan(plan_id: str) -> str:
    """Resumes a paused cluster plan and all its associated paused article jobs."""
    engine, LocalSession = _make_agent_session()
    import asyncio
    async def _run():
        try:
            async with LocalSession() as session:
                p = await session.get(ClusterPlan, plan_id)
                if not p: return json.dumps({"error": f"Plan {plan_id} not found."})
                p.status = "planning" # restart from planning or current step
                session.add(p)
                
                # Cascade to article jobs
                jobs_query = select(ArticleJob).where(ArticleJob.cluster_plan_id == plan_id)
                jobs = (await session.exec(jobs_query)).all()
                resumed_count = 0
                for job in jobs:
                    if job.status == JobStatus.paused:
                        job.status = JobStatus.queued
                        session.add(job)
                        resumed_count += 1

                await session.commit()
                return json.dumps({"status": "success", "message": f"Cluster plan {plan_id} resumed along with {resumed_count} associated jobs."})
        finally:
            await engine.dispose()
    return asyncio.run(_run())

def tool_delete_cluster_plan(plan_id: str) -> str:
    """Deletes a cluster plan completely, along with its associated article jobs."""
    engine, LocalSession = _make_agent_session()
    import asyncio
    async def _run():
        try:
            async with LocalSession() as session:
                p = await session.get(ClusterPlan, plan_id)
                if not p: return json.dumps({"error": f"Plan {plan_id} not found."})
                
                # Cascade to article jobs
                jobs_query = select(ArticleJob).where(ArticleJob.cluster_plan_id == plan_id)
                jobs = (await session.exec(jobs_query)).all()
                deleted_count = 0
                for job in jobs:
                    await session.delete(job)
                    deleted_count += 1

                await session.delete(p)
                await session.commit()
                return json.dumps({"status": "success", "message": f"Cluster plan {plan_id} deleted along with {deleted_count} associated jobs."})
        finally:
            await engine.dispose()
    return asyncio.run(_run())

def tool_modify_cluster_plan(plan_id: str, modifications_json: str) -> str:
    """Modifies a cluster plan properties. Expects modifications_json as a JSON string with keys to update."""
    engine, LocalSession = _make_agent_session()
    import asyncio
    async def _run():
        try:
            mods = json.loads(modifications_json)
            async with LocalSession() as session:
                p = await session.get(ClusterPlan, plan_id)
                if not p: return json.dumps({"error": f"Plan {plan_id} not found."})
                for k, v in mods.items():
                    if hasattr(p, k):
                        setattr(p, k, v)
                session.add(p)
                await session.commit()
                return json.dumps({"status": "success", "message": f"Cluster plan {plan_id} updated."})
        except Exception as e:
            return json.dumps({"error": str(e)})
        finally:
            await engine.dispose()
    return asyncio.run(_run())



def tool_reschedule_cluster_plan(plan_id: str, start_date: str, end_date: str = None) -> str:
    """Reschedules all jobs in a cluster plan."""
    engine, LocalSession = _make_agent_session()
    import asyncio
    from datetime import datetime, timedelta, time
    async def _run():
        try:
            async with LocalSession() as session:
                p = await session.get(ClusterPlan, plan_id)
                if not p: return json.dumps({"error": f"Plan {plan_id} not found."})
                
                jobs_query = select(ArticleJob).where(ArticleJob.cluster_plan_id == plan_id).order_by(ArticleJob.scheduled_at.asc().nulls_first())
                jobs = (await session.exec(jobs_query)).all()
                if not jobs: return json.dumps({"error": f"No jobs found for plan {plan_id}."})
                
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                start_dt = datetime.combine(start_dt.date(), time(9, 0))
                
                if end_date:
                    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                    end_dt = datetime.combine(end_dt.date(), time(9, 0))
                    if len(jobs) > 1:
                        total_seconds = (end_dt - start_dt).total_seconds()
                        interval_seconds = total_seconds / (len(jobs) - 1)
                        interval = timedelta(seconds=interval_seconds)
                    else:
                        interval = timedelta(days=1)
                else:
                    interval = timedelta(days=1 if len(jobs) > 45 else 2)
                    
                current_dt = start_dt
                for job in jobs:
                    job.scheduled_at = current_dt
                    session.add(job)
                    current_dt += interval
                
                if p.tasks:
                    from sqlalchemy.orm.attributes import flag_modified
                    tasks = list(p.tasks)
                    current_dt = start_dt
                    for t in tasks:
                        t["scheduled_at"] = current_dt.isoformat()
                        current_dt += interval
                    p.tasks = tasks
                    flag_modified(p, "tasks")
                    session.add(p)
                
                await session.commit()
                return json.dumps({"status": "success", "message": f"Successfully rescheduled {len(jobs)} jobs for plan {plan_id} starting from {start_date}."})
        except Exception as e:
            return json.dumps({"error": str(e)})
        finally:
            await engine.dispose()
    return asyncio.run(_run())
