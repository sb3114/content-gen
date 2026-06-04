# Content Engine — Software Architecture

## Overview

The application is a **FastAPI-based autonomous AI content pipeline** that researches, plans, writes, and publishes blog posts across WordPress, LinkedIn, and email newsletters. It combines two fundamentally different execution models:

1. **A deterministic, stage-driven pipeline** — for all article generation work (predictable, resumable, sequential stages).
2. **An agentic, tool-using conversation loop** — for the chat-driven planning interface (non-deterministic, reactive, multi-turn).

---

## Application Entrypoint

**File: `src/main.py`**

FastAPI app with 4 route groups and a lifespan manager:

```
FastAPI
├── /jobs  ── jobs_router    (all job CRUD, publishing, UI templates)
├── /auth  ── auth_router    (LinkedIn/Google OAuth)
├── /cal   ── calendar_router
└── /agent ── agent_router   (chat-based agent API)
```

On startup, two things happen:
1. `init_db()` — runs all database migrations (idempotent `ALTER TABLE IF NOT EXISTS` calls).
2. `start_scheduler()` — boots the APScheduler background clock.

---

## 1. The Deterministic Pipeline

This is the **core workhorse**. All article generation follows a fixed, linear sequence of stages. The orchestrator is the conductor.

```
Orchestrator (orchestrator.py)
│
├── Phase 1 — run_pipeline(job_id)
│   ├── Step 1: Research          → research.py
│   ├── Step 1.5: Viability Check (no LLM, purely numeric)
│   └── Keyword Gate              → PAUSE (pending_review) or auto-continue
│
└── Phase 2 — resume_pipeline(job_id)
    ├── Step 2: Planning          → planning.py
    ├── Step 3: Writing           → writing.py
    ├── Step 4: LinkedIn Adapt    → linkedin_adapt.py
    ├── Step 5: Newsletter Adapt  → newsletter_adapt.py
    ├── Step 5.5: Image Gen       → image_gen.py
    └── Human Review Gate         → PAUSE (pending_review) or auto-approve
        └── publish_job(job_id)
            ├── WordPress         → integrations/wordpress.py
            ├── LinkedIn          → integrations/linkedin.py
            └── Newsletter        → integrations/brevo.py
```

### Why "deterministic"?

Every stage is a pure function: given structured inputs from the DB, it produces a structured output and saves it. Stages are **idempotent** — if a job crashes and restarts, it checks what data already exists and skips completed steps. The orchestrator has explicit `if not job.content_plan:` guards throughout.

### Important: Pipeline stages are NOT agents

The `_PROMPT` strings in `writing.py`, `planning.py`, `linkedin_adapt.py`, etc. are **static prompt templates**, not agents. Each `run_*()` function does:

1. Fill `{placeholders}` in the template string using Python's `.format()`.
2. Make **one single LLM API call**.
3. Return the result.

There is no loop, no tool use, no decision-making by the LLM about what to do next. Code controls the flow entirely.

### How the DB is used as a state machine

The job's `status` field (`src/models/job.py`) drives the entire lifecycle:

| Status | Meaning |
|---|---|
| `queued` | Waiting in scheduler queue |
| `running` | Phase 1 active |
| `resuming` | Phase 2 active |
| `pending_review` | Paused at a human gate |
| `approved` | User approved — ready for publish |
| `publishing` | Publication in flight |
| `published` | Done |
| `failed` | Crashed with error |
| `rejected` | User declined |

The `current_step` string field (e.g. `"research"`, `"planning"`, `"keyword_confirmation"`) gives fine-grained visibility for the dashboard.

---

## 2. The Scheduler — How Jobs Run Autonomously

**File: `src/pipeline/scheduler.py`**

An **APScheduler AsyncIOScheduler** runs polling loops inside the FastAPI process:

| Job | Interval | Purpose |
|---|---|---|
| `check_pending_jobs` | Every 30 sec | Picks the oldest queued job and fires `run_pipeline(job_id)` as a background asyncio task |
| `check_scheduled_jobs` | Every 1 min | Finds jobs whose `scheduled_at` has passed and triggers `publish_job()` directly |
| `check_cluster_plans` | Every 2 min | Retries stalled cluster plan Stage 2 generation (LLM timeout recovery) |

### Queue safety rules

The scheduler enforces strict single-job-at-a-time execution:
- **Stale watchdog**: Any job stuck in `running` for >20 min is reset to `queued`.
- **Pipeline busy check**: If any job is `running` or `resuming`, nothing new starts.
- **10-minute pacing**: After a job completes, a 10-min cooldown before the next one starts.
- **Time window**: A configurable hour window (e.g. 9am–6pm) restricts when the queue fires.
- **Rate-limit gate**: If an LLM rate-limit exception is caught, the scheduler pauses itself until a recorded `rate_limit_until` timestamp passes.

> **Key design point**: Jobs are launched via `asyncio.create_task()`, so each pipeline runs concurrently with the scheduler loop but is constrained by the single-job gate.

---

## 3. Individual Pipeline Stages

Each stage is a **standalone Python module** with:
- A module-level `_PROMPT` string (the prompt template — a Python string with `{placeholders}`).
- A single async `run_*()` function that receives typed inputs and returns typed outputs.

### Stage breakdown

#### `research.py` — Deterministic Data Fetching
**No LLM call.** Delegates to:
- `KeywordResearcher` (`integrations/keywords.py`) → calls DataForSEO APIs, discovers focus keywords via a 5-stage Golden Ratio pipeline.
- `ArticleScraper` (`integrations/scraper.py`) → scrapes competitor URLs with `trafilatura`.

Both run in parallel with `asyncio.gather()`.

#### `planning.py` — Two Sequential LLM Calls
**Two LLM calls** using different tiers:

1. **Sonnet**: Given the topic, keyword data, and competitor excerpts, generates a full `ContentPlan` JSON (structured outline, angles, tone).
2. **Haiku**: Given the plan outline, refines and generates the final SEO title, meta description, and tags.

The result is a `ContentPlan` Pydantic model saved to the DB.

**Prompt location**: `planning.py` → `_PROMPT` (line 13). Dynamic sections injected via `.format()`.

#### `writing.py` — Single LLM Call (Sonnet)
**One LLM call** (most powerful model, highest quality).

**Prompt location**: `writing.py` → `_PROMPT` (line 11). A detailed instruction document covering:
- Information accuracy & citation rules
- SEO keyword density guidelines
- GEO (Generative Engine Optimization) best practices
- Style: paragraph ≤150 words, sentence variety, ≥35% transition words, simple vocabulary
- CTA requirements

Dynamic context injected at runtime:
- `company_context_section` — brand memory from `memory.py`
- `personalization_section` — user-provided stories/anecdotes
- `paa_section` — People Also Ask questions from keyword research
- `competitor_section` — competitor URLs for structural reference
- `style_memory_section` — rules learned from past user edits

#### `linkedin_adapt.py` — Single LLM Call (Sonnet)
Takes the content plan + first 600 words of the article.

**Prompt location**: `linkedin_adapt.py` → `_PROMPT` (line 14). Explicit rules for hook, body, "Discover more in comments" CTA, hashtags.

#### `newsletter_adapt.py` — Single LLM Call (Haiku or Sonnet)
Two prompts depending on newsletter type (`update` vs `summary`).

**Prompt location**: `newsletter_adapt.py` → `_UPDATE_PROMPT` and `_SUMMARY_PROMPT`.

#### `refinement.py` — Single LLM Call (Haiku)
Called from the review page when the user requests edits in natural language. Receives the full article HTML, current LinkedIn post, and user feedback. Returns updated versions of both.

**Prompt location**: `refinement.py` → `_PROMPT` (line 17).

#### `image_gen.py` — Gemini Imagen API
Uses Gemini's Imagen model to generate 3 candidate images. Images are saved to `src/ui/static/generated_images/` and paths stored in `job.generated_images`.

#### `summarize.py` — Published Content Cross-Link Memory
Queries all published jobs and uses a Haiku call to generate a compact memory blob describing existing content. Injected into future writing prompts to enable internal linking suggestions.

---

## 4. The LLM Abstraction Layer

**File: `src/pipeline/llm.py`**

All LLM calls in the deterministic pipeline go through a **single router function**:

```python
text, usage = await call_llm(
    prompt=str,
    tier="sonnet" | "haiku",    # cost/quality tier
    system_instruction=str,      # optional separate system prompt
    response_schema=PydanticModel, # enforces structured JSON output
    db_settings=CompanySettings
)
```

### Provider routing

| Setting | `sonnet` tier | `haiku` tier |
|---|---|---|
| `llm_provider = "gemini"` | `gemini-2.5-pro` | `gemini-2.0-flash` |
| `llm_provider = "claude"` | `claude-sonnet` via CLI | `claude-haiku` via CLI |

**Claude is invoked via subprocess** (`claude -p "<prompt>" --model <model>`) using a Claude OAuth setup token — not the Anthropic API directly. The subprocess runs asynchronously via `asyncio.create_subprocess_exec()` with a 360-second timeout.

If Claude Sonnet hits a rate limit and `allow_fallback_to_haiku` is enabled, it automatically retries with Haiku.

### Structured JSON output

When `response_schema` is a Pydantic model:
- **Gemini**: Sets `response_mime_type: application/json` and passes the cleaned schema natively.
- **Claude**: Schema is serialized to JSON and appended to the prompt as an instruction.

---

## 5. The Agentic Workflow — Chat Agent

**File: `src/pipeline/agent.py`**

The only **true agentic workflow** in the application. It is a multi-turn, tool-calling conversational loop exposed through the Agent chat page.

### What makes it "agentic"

- The LLM **decides which tool to call** based on natural language — code does not prescribe the sequence.
- It maintains **conversation history** across turns.
- It can **chain multiple tool calls** to accomplish complex goals.

### Agent tools

| Tool | What it does |
|---|---|
| `tool_create_jobs` | Creates `ArticleJob` DB records |
| `tool_list_jobs` | Queries and returns job statuses |
| `tool_edit_job` | Modifies a job's schedule or publish targets |
| `tool_delete_job` | Removes a job |
| `tool_generate_90_day_plan` | Triggers `run_cluster_plan_stage1()` |
| `tool_approve_and_schedule_latest_plan` | Approves the pending cluster plan and batch-creates jobs |

### Agent system prompt

**Defined inline in `agent.py` → `get_agent_chat()`** (line ~615). Built dynamically at session creation from the brand context cache:

```python
sys_instr = "You are the Content Engine Agent..."
# Dynamically appended from settings:
+ company_description
+ marketing_strategy
+ tone_of_voice
+ icp
+ core_pillars
+ audiences
```

### Agent backend routing

| Provider | Implementation |
|---|---|
| **Gemini** | `google.genai` native `function_declarations` — Gemini handles tool routing automatically |
| **Claude** | Custom `ClaudeChat` class — hand-rolled conversation history, tool call parsing from LLM response |

---

## 6. The Cluster Planning Pipeline

**File: `src/pipeline/cluster_orchestrator.py`**

The 90-day Hub & Spoke strategy generation has its own two-stage pipeline with its own human review gates:

```
Stage 1 — run_cluster_plan_stage1(plan_id):  Keyword Research
├── LLM (Haiku): Deconstruct brand pillars into seed keywords
├── DataForSEO: Fetch keyword suggestions per pillar
├── LLM (Haiku): Filter competitor keywords for brand relevance
├── LLM (Sonnet): Fallback simulation if no DataForSEO credentials
└── → status: "keyword_review"  ← HUMAN GATE (curate keywords in UI)

Stage 2 — run_cluster_plan_stage2(plan_id):  Strategy & Scheduling
├── LLM (Sonnet): Group approved keywords into Hub & Spoke article titles
├── Deterministic: Calendar distribution across 90 days (math, no LLM)
└── → status: "cluster_review"  ← HUMAN GATE (approve cluster before jobs created)
```

Each stage is triggered by a different user action. Stage 2 is retried automatically by the scheduler if it times out.

---

## 7. Memory System

**File: `src/pipeline/memory.py`**

Two persistent memory stores on disk (`data/agent_memory/`):

| Memory | File | Purpose |
|---|---|---|
| **Style Memory** | `style_learning_memory.md` | Writing rules learned from user edits — injected into writing & refinement prompts |
| **Brand Context Cache** | `brand_context_memory.json` | Snapshot of brand settings for fast synchronous access in prompt construction |

### Self-improving feedback loop

1. User manually edits article on review page → `record_edit_feedback()` → Haiku diffs old vs. new → extracts 1–3 rules → merges into `style_learning_memory.md`.
2. User sends corrective chat message → `record_style_feedback()` → same flow.

Future writing calls automatically include these accumulated style rules.

---

## 8. Integrations Layer

**`src/integrations/`** — pure I/O adapters, no LLM logic:

| File | Purpose |
|---|---|
| `wordpress.py` | WordPress REST API — create/update posts, upload media, set Yoast SEO meta fields |
| `linkedin.py` | LinkedIn Share API — post articles, delete posts, add comment with article URL |
| `brevo.py` | Brevo email API — send HTML newsletters to contact lists |
| `keywords.py` | DataForSEO + pytrends — keyword discovery, SERP data, PAA harvesting |
| `scraper.py` | trafilatura competitor URL scraper |
| `google.py` | Google Search Console + Business Profile integrations |

---

## 9. Prompt Locations — Quick Reference

| Prompt | File | Variable / Location |
|---|---|---|
| Planning Architect | `pipeline/planning.py` | `_PROMPT` (line 13) |
| Writer | `pipeline/writing.py` | `_PROMPT` (line 11) |
| LinkedIn Adapter | `pipeline/linkedin_adapt.py` | `_PROMPT` (line 14) |
| Newsletter Update | `pipeline/newsletter_adapt.py` | `_UPDATE_PROMPT` |
| Newsletter Summary | `pipeline/newsletter_adapt.py` | `_SUMMARY_PROMPT` |
| Editor/Refinement | `pipeline/refinement.py` | `_PROMPT` (line 17) |
| 90-Day Strategy (chat tool) | `pipeline/agent.py` | `_90_DAY_STRATEGY_PROMPT` (line 19) |
| Agent System Prompt | `pipeline/agent.py` | `get_agent_chat()` inline (~line 615) |
| Brand Pillar Deconstructor | `pipeline/cluster_orchestrator.py` | inline in `run_cluster_plan_stage1()` |
| Cluster Strategy Planner | `pipeline/cluster_orchestrator.py` | inline in `run_cluster_plan_stage2()` |
| Competitor Keyword Filter | `pipeline/cluster_orchestrator.py` | `filter_relevant_competitor_keywords()` |
| SEO Title Generator | `pipeline/orchestrator.py` | `generate_punchy_seo_title()` |
| Style Feedback Extractor | `pipeline/memory.py` | `record_style_feedback()` |
| Edit Diff Analyzer | `pipeline/memory.py` | `record_edit_feedback()` |

---

## 10. System Diagram

```
┌───────────────────────────────────────────────────────────────┐
│                        FastAPI App                            │
│  /jobs (UI + API)  │  /agent (chat)  │  /auth  │  /calendar  │
└────────────────┬────────────────┬──────────────────────────────┘
                 │                │
         ┌───────▼──────┐  ┌──────▼───────────────────────────┐
         │  Scheduler   │  │     Chat Agent (agentic loop)    │
         │  (30s poll)  │  │  Gemini native function calling  │
         └───────┬───────┘  │  or ClaudeChat (custom class)   │
                 │          └──────┬────────────────────────────┘
         ┌───────▼──────────────── │──────────────────────────┐
         │    Deterministic Pipeline (orchestrator.py)        │
         │                         │                          │
         │  Phase 1: Research ──── │ ── Keyword Gate (pause) │
         │  Phase 2: Planning ─────┘                          │
         │           Writing                                  │
         │           LinkedIn Adapt                           │
         │           Newsletter Adapt                         │
         │           Image Gen                                │
         │           Human Review Gate (pause)               │
         │           Publishing                               │
         └────────────────────────────────────────────────────┘
                 │
         ┌───────▼──────────────────────────────────┐
         │              LLM Router (llm.py)          │
         │  call_llm(prompt, tier)                   │
         │  ├─ Claude (subprocess, OAuth CLI)        │
         │  └─ Gemini (google-generativeai SDK)      │
         └───────────────────────────────────────────┘
                 │
         ┌───────▼──────────────────────────────────┐
         │           Integrations Layer              │
         │  WordPress │ LinkedIn │ Brevo │ DataForSEO│
         └───────────────────────────────────────────┘
```
