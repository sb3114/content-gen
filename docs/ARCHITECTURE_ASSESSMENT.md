# Architectural Assessment — Content Engine

## What the Current Architecture Is

A monolithic **FastAPI web app** with:
- A **polling scheduler** (APScheduler) that runs pipeline stages sequentially as `asyncio.create_task()` coroutines inside the same process.
- Shared PostgreSQL state machine to coordinate job lifecycle.
- **Single-shot LLM calls** (not agents) for each content stage — `writing.py`, `planning.py`, etc. each make one LLM call with a static prompt template.
- One **true agentic loop** (the chat page in `agent.py`).
- **Subprocess invocation** of the Claude CLI for LLM calls.

---

## Current Architecture: Honest Assessment

### ✅ What it gets right

| Strength | Why it matters |
|---|---|
| **Deterministic, resumable pipeline** | If it crashes mid-run, it picks up exactly where it left off. Very resilient for long LLM jobs. |
| **DB as state machine** | Single source of truth. UI, scheduler, and pipeline all read the same `status` field. Easy to reason about. |
| **Single `call_llm()` router** | Swapping LLM providers is a one-field settings change. Gemini ↔ Claude without touching stage code. |
| **Prompt-as-string templates** | Dead simple to read, edit, version-control, and A/B test. No framework magic hiding them. |
| **Human-in-the-loop gates** | The `pending_review` pause points are exactly correct for a content workflow. |
| **Sequential queue with pacing** | Prevents API rate-limit hammering. Practical for a small-team setup. |
| **FastAPI + SQLModel** | Async-native, Pydantic integration, correct session lifecycle (short sessions, never held open during LLM calls). |

### ❌ What it gets wrong

#### 1. Everything runs in one process
The scheduler, web server, and all pipeline stages share one Python process and event loop. A stuck LLM call (360-second Claude timeout) can degrade API response times for the UI. A hard crash takes down everything at once.

#### 2. APScheduler polling is the wrong tool for job queues
APScheduler is a **cron scheduler**, not a job queue. Using it as one requires the stale-watchdog hack, the 10-minute pacing logic, and manual queue position renumbering. These are all symptoms of using the wrong primitive.

```python
# This is fighting APScheduler's design:
INTER_JOB_DELAY = timedelta(minutes=10)  # manual pacing
stale_cutoff = datetime.utcnow() - STALE_THRESHOLD  # watchdog
stale.status = JobStatus.queued  # manual reset
```

A proper job queue handles all of this natively.

#### 3. Claude via subprocess is fragile and expensive
```python
proc = await asyncio.create_subprocess_exec("claude", "-p", full_prompt, ...)
```
- Spawns an OS process per LLM call.
- 360-second timeout blocks an async task slot.
- ANSI stripping, return code parsing, stderr parsing — all custom glue code.
- Cannot stream responses.
- Cannot batch calls.
- The Claude CLI is a developer tool, not a production API. It can update and silently break at any time.

#### 4. Token waste — full context re-sent on every call
Every LLM call rebuilds the **full company context** from scratch in the prompt:
```python
ctx_section = f"## Company Context\n{company_context}\n"
# Injected into: planning, writing, linkedin_adapt, newsletter_adapt
```
Planning + Writing + LinkedIn + Newsletter each re-send the same 2,000+ token context prefix as billable input tokens.

#### 5. No streaming
All LLM calls are fire-and-wait. The user sees a spinner for 30–120 seconds with no feedback that anything is happening.

#### 6. File-based memory is fragile
Style memory is a markdown file. Brand context is a JSON file. These can be corrupted, won't survive a container rebuild unless a volume is mounted correctly, and cannot be queried or rolled back.

#### 7. Planning stage uses two sequential LLM calls where one would do
```python
# Call 1 (Sonnet, ~3000 tokens): Generate full ContentPlan JSON
plan_text, usage_sonnet = await call_llm(prompt=prompt, tier="sonnet", response_schema=ContentPlan)
# Call 2 (Haiku, ~800 tokens): Re-read outline, generate title + tags
haiku_text, usage_haiku = await call_llm(prompt=haiku_prompt, tier="haiku", use_json=True)
```
The Haiku call just reads Call 1's output. It can be folded into the Sonnet schema, saving a full round-trip.

#### 8. Mixed Google SDK versions
The codebase uses both `google.generativeai` (old, deprecated SDK) and `google.genai` (new SDK) in different files. These are different library versions with different APIs — a maintenance hazard.

---

## Recommended Architecture

### Core principle: separate concerns by execution characteristics

```
┌────────────────┐     ┌──────────────────────────────────┐
│   FastAPI App  │────▶│     Message Queue (Redis/RQ)     │
│  (web only)    │     │  enqueue_job() → worker picks up │
└────────────────┘     └──────────────────┬───────────────┘
                                          │
                       ┌──────────────────▼───────────────┐
                       │    Worker Process (separate)     │
                       │  pipeline stages run here        │
                       │  crash here = web stays up       │
                       └──────────────────────────────────┘
```

---

### 1. Replace APScheduler + asyncio tasks → RQ (Redis Queue)

**Recommended: RQ** — simpler than Celery, perfect fit for this scale.

```python
# Enqueue from the API:
from rq import Queue
q = Queue(connection=Redis())
q.enqueue(run_pipeline, job_id, job_timeout=1800)

# Worker runs as a separate process:
# rq worker --with-scheduler
```

**What you get for free:**
- Automatic retry with backoff (no stale-watchdog hack needed).
- Job priorities — manually approved jobs publish immediately; auto-queue waits.
- Worker process isolation — LLM crashes don't affect the web server.
- Built-in job result storage.
- `rq-scheduler` replaces `check_scheduled_jobs`.
- `rq-dashboard` gives you a monitoring UI out of the box.

---

### 2. Replace Claude subprocess → Anthropic Python SDK

```python
import anthropic
client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

message = await client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=8192,
    messages=[{"role": "user", "content": prompt}],
    stream=True
)
```

**What you gain:**
- Native async — no subprocess, no OS process overhead.
- Streaming support — user sees text appearing in real time.
- Proper typed exceptions (`RateLimitError`, `APIError`).
- Native token usage metadata.
- No ANSI stripping, no shell injection risk, no 360-second subprocess timeout.

---

### 3. Token cost optimisation — Prompt Caching (Highest ROI change)

Both Gemini and Claude support **prompt caching** — repeated prefixes cost ~10% of normal input token price.

```python
# Gemini: cached_content
from google.generativeai import caching
cache = caching.CachedContent.create(
    model="gemini-2.5-pro",
    contents=[company_context_section],  # static brand context
    ttl=datetime.timedelta(hours=1),
)
model = genai.GenerativeModel.from_cached_content(cache)
response = await model.generate_content_async(dynamic_prompt)
```

```python
# Anthropic: cache_control on system
message = await client.messages.create(
    model="claude-sonnet-4-5",
    system=[{
        "type": "text",
        "text": company_context_section,
        "cache_control": {"type": "ephemeral"}  # 5-min cache
    }],
    messages=[{"role": "user", "content": dynamic_prompt}]
)
```

**Estimated savings on your workflow:**
- Brand context (~2,000 tokens) injected into 4 pipeline stages per job.
- With caching: those tokens cost ~10% on repeat calls within the cache window.
- Saves ~6,000 billable input tokens per job.
- Compounds significantly at scale.

---

### 4. Eliminate the redundant Haiku post-processing call in planning

Currently:
```
Call 1 (Sonnet, ~3,000 tokens): Generate ContentPlan JSON
Call 2 (Haiku, ~800 tokens):    Re-read outline → generate title + tags
```

Fold tags into the Sonnet schema. One call instead of two.

```python
class ContentPlan(BaseModel):
    chosen_title: str         # already exists
    focus_keyword: str        # already exists
    meta_description: str     # already exists
    tags: List[str]           # ADD THIS — Sonnet generates directly
    ...
```

Saves: 1 full Haiku round-trip (~800 input tokens + ~200 output tokens + network latency) per job.

---

### 5. Move style/brand memory to PostgreSQL

```python
class AgentMemory(SQLModel, table=True):
    id: int = Field(primary_key=True)
    memory_type: str    # "style" | "brand"
    content: str        # markdown/json content
    updated_at: datetime
```

- Survives container restarts without volume mount dependency.
- Can be queried, versioned, rolled back.
- One extra `SELECT` per job — negligible overhead.

---

### 6. Streaming responses to the UI

Replace the spinner with **Server-Sent Events (SSE)**:

```python
from sse_starlette.sse import EventSourceResponse

@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    async def event_generator():
        async for chunk in stream_llm_call(prompt):
            yield {"data": chunk}
    return EventSourceResponse(event_generator())
```

The writing step could stream directly to the review page — users see the article being written live instead of waiting 90 seconds.

---

### 7. Workflow engine for pipeline (optional, future)

If pipeline complexity grows (parallel stages, conditional branches, complex retry logic), consider a proper workflow engine:

| Tool | Fit | Trade-off |
|---|---|---|
| **Prefect** | ✅ Python-native, retries, observability UI | Adds infra dependency |
| **Temporal** | ✅ Best-in-class durability, exactly-once semantics | Complex setup, Go/Java first |
| **LangGraph** | ✅ Designed specifically for LLM pipelines | Tied to LangChain ecosystem |
| **Current custom orchestrator** | 🟡 Works at current scale | You own the state machine |

For current scale (single-tenant, <100 jobs/day), **Prefect** is the sweet spot:

```python
from prefect import task, flow

@task(retries=2, retry_delay_seconds=60)
async def planning_task(topic, keyword_data, scraped_content):
    return await run_planning(...)  # keeps all your existing prompt templates

@flow
async def content_pipeline(job_id):
    research = await research_task(job_id)
    plan = await planning_task(**research)
    article = await writing_task(plan)
    ...
```

You keep all your prompt templates. You just replace the handwritten state machine with Prefect's.

---

## Should You Move to a Proper Agentic Framework (LangGraph / Google ADK)?

This is the most important architectural question for the pipeline's future. The answer requires separating the two very different parts of the codebase.

---

### Part 1: The Content Pipeline (research → plan → write → publish)

**Verdict: Keep it scripted. Do NOT move to LangGraph or ADK.**

Here is why, component by component:

#### What an agentic framework would give you here

LangGraph and Google ADK are designed for workflows where the **LLM must decide** what to do next — which tool to call, whether to loop, which branch to take. They add value when control flow is non-deterministic.

#### Why the content pipeline doesn't need that

The content pipeline is **always the same fixed sequence**:

```
Research → Plan → Write → LinkedIn → Newsletter → Images → Publish
```

There are no dynamic branches where the LLM needs to decide what step comes next. Every decision is either:
- **A fixed code rule** (`if not job.content_plan: run planning`), or
- **A human decision** at the review gate.

Handing this to an agent framework would:
- Add non-determinism where you want predictability.
- Make it harder to resume mid-pipeline after a crash (a key strength of the current design).
- Add a framework dependency that hides the pipeline logic inside library abstractions.
- Make debugging significantly harder — when a stage fails in an agent loop, the failure is inside the framework's execution graph, not your Python stack trace.

#### The cost of adopting LangGraph here

```python
# Current: obvious, debuggable, resumable
async def run_writing(plan, context):
    prompt = _PROMPT.format(...)
    return await call_llm(prompt, tier="sonnet")

# With LangGraph: added abstraction for zero benefit
from langgraph.graph import StateGraph
from langgraph.checkpoint.postgres import PostgresSaver

graph = StateGraph(ContentState)
graph.add_node("write", writing_node)
graph.add_edge("plan", "write")
# Now you need to manage: state schema, checkpointer, graph compilation,
# streaming events, LangSmith tracing setup...
# All for a step that was 4 lines of code.
```

You would be paying the full cost of the framework (state schema management, graph compilation, checkpointing, event streaming) for a workflow that never needs dynamic control flow.

**The current scripted approach is correct for this part.** The only improvement needed is replacing APScheduler with RQ and moving to a proper workflow engine like Prefect — not an agent framework.

---

### Part 2: The Cluster Planning Pipeline (90-day strategy)

**Verdict: Keep it scripted today. Consider Google ADK if it grows more complex.**

The cluster orchestrator's docstrings call Stage 1 and Stage 2 "agents" but they are actually scripted multi-step functions. Concretely:

```python
# cluster_orchestrator.py imports — no agent framework at all:
import json
import logging
from src.pipeline.llm import call_llm   # only LLM dependency
```

Stage 1 makes 3–4 LLM calls in a fixed Python `for` loop. Stage 2 makes 1 LLM call then does calendar maths. Code controls every branch.

**When would ADK / LangGraph actually add value here?**

If the cluster planning pipeline evolved to include:
- Dynamic sub-agent specialisation (e.g. a separate SERP agent, a competitor analysis agent, a trend forecasting agent running in parallel and synthesising results).
- Self-correcting loops (e.g. if generated keyword clusters are low quality, the LLM decides to re-run discovery with different seeds).
- Branching based on LLM reasoning (e.g. the LLM decides whether to run competitor analysis based on the brand context).

In that case, **Google ADK** would be the better choice over LangGraph for this codebase because:
- You already use Gemini as the primary LLM — ADK is native to the Gemini ecosystem.
- ADK's multi-agent patterns (sequential, parallel, hierarchical) map cleanly to the Hub & Spoke planning model.
- ADK has first-class support for `google-genai` SDK, avoiding the mixed SDK problem.

LangGraph would be the choice if you move to Claude as the primary LLM, since it's provider-agnostic and has better LangChain/Anthropic tooling.

---

### Part 3: The Chat Agent (agent.py)

**Verdict: This is the one place where an agent framework IS appropriate — but the current implementation is good enough.**

The chat agent is already a real agentic loop with tool use. The current implementation uses:
- **Gemini native function calling** — correct and efficient for Gemini.
- **Custom `ClaudeChat` class** — hand-rolled tool routing for Claude.

The ClaudeChat class is the weak point. If Claude remains a supported provider, replacing it with **LangGraph** (which handles tool call routing and history management natively for any provider) would eliminate the custom parsing code. Google ADK would work equally well if Gemini stays the primary provider.

But the agent's tools (create job, list jobs, approve plan) are simple DB operations — no complex multi-step tool chains. The current implementation handles this scope well.

---

### Framework Decision Matrix

| Pipeline Component | Current Approach | LangGraph | Google ADK | Verdict |
|---|---|---|---|---|
| **Content pipeline** (research→publish) | Scripted Python | ❌ Overkill, hurts resumability | ❌ Overkill, hurts resumability | ✅ **Keep scripted** |
| **Cluster planner** (90-day strategy) | Scripted Python | 🟡 Viable if it grows complex | 🟡 Better fit if Gemini stays primary | 🟡 **Keep today, revisit if stages multiply** |
| **Chat agent** | Gemini native + custom ClaudeChat | 🟡 Replaces ClaudeChat cleanly | ✅ Natural fit for Gemini primary | 🟡 **Optional improvement, not urgent** |

### The honest one-line answer

> **No — do not migrate to LangGraph or Google ADK today.** The content pipeline is a scripted, deterministic workflow that intentionally should not have a dynamic control flow. The correct next steps are better infrastructure primitives (RQ, Anthropic SDK, prompt caching) — not an agent framework.

---

## Framework Assessment

### FastAPI ✅ Keep
Async-native, excellent OpenAPI generation, Pydantic v2 integration, SSE support. Right tool for the web layer.

### SQLModel / Async SQLAlchemy ✅ Keep
Correct choice. The pattern of short, isolated sessions (opened per operation, never held open during LLM calls) is correct and avoids connection pool starvation.

### Pydantic schemas ✅ Keep
Using Pydantic for structured LLM output validation and native Gemini schema support is the right approach.

### APScheduler ❌ Replace
A cron scheduler being used as a task queue. Replace with **RQ + Redis**.

### Claude CLI subprocess ❌ Replace
A developer CLI tool used as a production API. Replace with **Anthropic Python SDK**.

### `google-generativeai` ⚠️ Consolidate
Mixed usage of old `google.generativeai` and new `google.genai` SDK. Standardise on `google-genai` (newer). The old SDK is effectively deprecated.

---

## Recommended Changes — Priority Order

```
Current                           Recommended
─────────────────────────────     ──────────────────────────────────
FastAPI (web + worker)      →     FastAPI (web only)
APScheduler (in-process)    →     RQ + Redis (separate worker process)
Claude subprocess           →     Anthropic SDK (async, streaming)
google.generativeai (old)   →     google-genai (new, unified)
File-based memory           →     PostgreSQL (AgentMemory table)
No caching                  →     Gemini/Claude prompt caching
Full context per call       →     Cached prefix + dynamic suffix
Two planning LLM calls      →     One call with extended schema
No streaming                →     SSE streaming to UI
Hand-rolled orchestrator    →     Prefect flows (optional, when needed)
```

| Priority | Change | Impact |
|---|---|---|
| 🔴 **High** | Replace Claude subprocess → Anthropic SDK | Reliability, streaming, proper error handling |
| 🔴 **High** | Add prompt caching (Gemini/Claude) | -30–60% token costs on brand context |
| 🔴 **High** | Separate worker process (RQ + Redis) | Crash isolation, proper retries, no stale-watchdog hacks |
| 🟡 **Medium** | Standardise on `google-genai` SDK | Maintainability, single dependency |
| 🟡 **Medium** | Move memory to PostgreSQL | Reliability, query-ability |
| 🟡 **Medium** | Fold Haiku post-processing into Sonnet call | -1 LLM call per job |
| 🟢 **Low** | Add SSE streaming to UI | UX — see writing happen in real time |
| 🟢 **Low** | Adopt Prefect for pipeline orchestration | Only if pipeline stages multiply significantly |
| ❌ **Not recommended** | Migrate content pipeline to LangGraph/ADK | Deterministic pipelines don't benefit from agent frameworks — adds complexity for no gain |
| 🟢 **Low / Future** | Adopt Google ADK for cluster planner | Only if the planning pipeline gains dynamic branching or parallel sub-agents |
