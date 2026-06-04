# Content Engine

> **AI-powered autonomous content pipeline** — researches, plans, writes, and publishes SEO-optimised blog posts to WordPress, LinkedIn, and email newsletters with human-in-the-loop review gates.

Built with **FastAPI · PostgreSQL · Google Gemini · Claude · Docker**.

---

## Table of Contents

- [Features](#-features)
- [Architecture Overview](#-architecture-overview)
- [Project Structure](#-project-structure)
- [Tech Stack](#-tech-stack)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [How It Works](#-how-it-works)
- [Publishing Integrations](#-publishing-integrations)
- [SEO Pipeline](#-advanced-5-stage-seo-research--discovery-pipeline)
- [Chat Agent](#-chat-agent)
- [Documentation](#-documentation)
- [Data Persistence](#-data-persistence)

---

## ✨ Features

| Feature | Description |
|---|---|
| **Autonomous Content Pipeline** | End-to-end: keyword research → planning → writing → LinkedIn/newsletter adaptation → image generation → publish |
| **Human-in-the-Loop Gates** | Mandatory review checkpoints at keyword selection and final content approval |
| **90-Day Content Strategy** | Chat agent generates and schedules a full Hub & Spoke content cluster plan |
| **Multi-Channel Publishing** | Simultaneous publish to WordPress (self-hosted), LinkedIn, and Brevo email newsletters |
| **Yoast SEO Integration** | Native Yoast meta field updates (`_yoast_wpseo_title`, `_yoast_wpseo_metadesc`) with automatic punchy title generation for long H1s |
| **AI Image Generation** | Generates 3 candidate images per article using Gemini Imagen; user selects the best |
| **Style Memory** | Learns from your manual edits and feedback to improve future articles automatically |
| **LLM Provider Switching** | Toggle between Google Gemini and Claude (Sonnet/Haiku tiers) in Settings — no code changes |
| **Sequential Job Queue** | Rate-limit-aware, paced job scheduler with stale-job recovery |
| **Brand Context Injection** | All LLM calls are grounded in your company description, ICP, tone of voice, and core pillars |

---

## 🏗️ Architecture Overview

The application uses **two distinct execution models**:

### 1. Deterministic Content Pipeline
A fixed, resumable sequence of stages driven by `orchestrator.py`. Each stage is an independent Python module that reads from the database, makes one LLM call, saves its output, and exits. If a stage crashes, it restarts exactly where it left off.

```
Research → Planning → Writing → LinkedIn Adapt → Newsletter Adapt → Image Gen
    ↓                                                                      ↓
Keyword Gate (human review)                              Content Review Gate (human)
                                                                           ↓
                                                                      Publish
```

### 2. Agentic Chat Interface
A multi-turn, tool-calling conversational loop powered by Gemini native function calling (or a custom Claude equivalent). The LLM decides which tools to call based on natural language — it is not scripted.

> 📄 See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system walkthrough including all prompt locations, scheduler logic, and the cluster planning pipeline.

> 📄 See [`docs/ARCHITECTURE_ASSESSMENT.md`](docs/ARCHITECTURE_ASSESSMENT.md) for a critical assessment of the current design with pros, cons, and a recommended migration roadmap.

---

## 📁 Project Structure

```
content-creator/
├── src/
│   ├── main.py                    # FastAPI app entrypoint & lifespan
│   ├── config.py                  # Pydantic settings (env vars)
│   ├── database.py                # Async SQLAlchemy engine & migrations
│   │
│   ├── api/
│   │   ├── jobs.py                # All job CRUD, review, publish endpoints
│   │   ├── agent.py               # Chat agent SSE API
│   │   ├── auth.py                # LinkedIn & Google OAuth flows
│   │   └── calendar.py            # Calendar view endpoint
│   │
│   ├── pipeline/
│   │   ├── orchestrator.py        # Phase 1 & 2 pipeline conductor
│   │   ├── scheduler.py           # APScheduler job queue & polling loops
│   │   ├── llm.py                 # LLM router (Gemini / Claude)
│   │   ├── research.py            # Step 1: keyword research + scraping
│   │   ├── planning.py            # Step 2: content plan generation
│   │   ├── writing.py             # Step 3: full article generation
│   │   ├── linkedin_adapt.py      # Step 4: LinkedIn post adaptation
│   │   ├── newsletter_adapt.py    # Step 5: email newsletter adaptation
│   │   ├── image_gen.py           # Step 5.5: AI image generation (Gemini Imagen)
│   │   ├── refinement.py          # Review-page editor (user feedback → LLM edit)
│   │   ├── cluster_orchestrator.py# 90-day Hub & Spoke cluster planner
│   │   ├── agent.py               # Chat agent tools & session management
│   │   ├── memory.py              # Style memory + brand context cache
│   │   ├── summarize.py           # Published content cross-link memory
│   │   └── scheduling.py          # Calendar slot calculation utilities
│   │
│   ├── integrations/
│   │   ├── wordpress.py           # WordPress REST API client
│   │   ├── linkedin.py            # LinkedIn API client
│   │   ├── brevo.py               # Brevo email API client
│   │   ├── keywords.py            # DataForSEO + pytrends keyword research
│   │   ├── scraper.py             # trafilatura competitor scraper
│   │   └── google.py             # Google Search Console & Business Profile
│   │
│   ├── models/
│   │   ├── job.py                 # ArticleJob & ClusterPlan SQLModel tables
│   │   └── settings.py            # CompanySettings SQLModel table
│   │
│   ├── schemas/
│   │   └── content_plan.py        # Pydantic schemas (ContentPlan, LinkedInPostSchema, etc.)
│   │
│   └── ui/
│       ├── templates/             # Jinja2 HTML templates
│       └── static/                # CSS, JS, generated images
│
├── data/
│   └── agent_memory/              # Style memory & brand context cache files
│
├── docs/
│   ├── ARCHITECTURE.md            # Full system architecture walkthrough
│   └── ARCHITECTURE_ASSESSMENT.md # Critical assessment & recommended improvements
│
├── scripts/
│   └── set-secrets.sh.example     # Shell secrets template
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Web Framework** | FastAPI 0.115 (async, Pydantic v2) |
| **Database** | PostgreSQL 16 via asyncpg + SQLModel |
| **LLM — Gemini** | `google-generativeai` + `google-genai` SDK |
| **LLM — Claude** | Claude CLI (subprocess via OAuth token) |
| **Image Generation** | Gemini Imagen (`google-genai` SDK) |
| **Keyword Research** | DataForSEO API + pytrends |
| **Competitor Scraping** | trafilatura |
| **Scheduling** | APScheduler (AsyncIOScheduler) |
| **Email** | Brevo (Sendinblue) API |
| **Templating** | Jinja2 |
| **Containerisation** | Docker + Docker Compose |

---

## 🚀 Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/install/)
- A Google Gemini API key (`GEMINI_API_KEY`)
- WordPress site with Application Passwords enabled
- LinkedIn OAuth credentials

### 1. Clone and configure

```bash
# Copy example env file
cp .env.example .env

# Copy and fill in your secrets
cp scripts/set-secrets.sh.example scripts/set-secrets.sh
# Edit set-secrets.sh with your actual values, then:
source scripts/set-secrets.sh
```

### 2. Required environment variables

These must be exported to your shell before running Docker Compose:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export POSTGRES_PASSWORD="your-strong-db-password"
export SECRET_KEY="your-random-secret-key"
export WORDPRESS_USERNAME="your-wp-username"
export WORDPRESS_APP_PASSWORD="xxxx xxxx xxxx xxxx xxxx xxxx"
export LINKEDIN_CLIENT_ID="your-linkedin-client-id"
export LINKEDIN_CLIENT_SECRET="your-linkedin-client-secret"

# Optional
export DATAFORSEO_LOGIN="your-dataforseo-login"
export DATAFORSEO_PASSWORD="your-dataforseo-password"
```

### 3. Start the application

```bash
docker compose up --build
```

Add `-d` for detached mode.

| Service | URL |
|---|---|
| **App** | http://localhost:8080 |
| **PostgreSQL** | `localhost:5433` |

> On first start, `init_db()` runs all schema migrations automatically. No manual migration steps needed.

---

## ⚙️ Configuration

All runtime settings (LLM provider, WordPress credentials, LinkedIn tokens, Brevo API, etc.) are managed through the **Settings page** in the UI at `/settings`. Changes are persisted to the `company_settings` database table and take effect immediately.

### Settings categories

| Section | What it configures |
|---|---|
| **Brand & Strategy** | Company description, marketing strategy, ICP, tone of voice, core pillars |
| **LLM Settings** | Provider (Gemini/Claude), API keys, model fallback behaviour, image generation restrictions |
| **WordPress** | Site URL, username, application password, author ID/name, Yoast Plugin toggle |
| **LinkedIn OAuth** | Client ID/secret, access token, person URN |
| **Brevo Email** | API key, sender details, contact list IDs |
| **DataForSEO** | Login credentials for live keyword research |
| **Queue Window** | Permitted hours for autonomous job processing |

---

## ⚙️ How It Works

### Job Lifecycle

```
queued → running → (keyword gate) → pending_review → resuming →
    (content review gate) → pending_review → approved → publishing → published
```

Each status transition corresponds to a specific pipeline action. The UI reflects the current `status` and `current_step` in real time.

### LLM Tiers

All LLM calls route through `src/pipeline/llm.py`:

| Tier | Gemini | Claude | Used for |
|---|---|---|---|
| `sonnet` | `gemini-2.5-pro` | `claude-sonnet` | Planning, writing, strategy (high quality) |
| `haiku` | `gemini-2.0-flash` | `claude-haiku` | Tags, metadata, categorisation (fast & cheap) |

Switch between Gemini and Claude in Settings with zero code changes.

### Style Memory (Self-Improving Writer)

Every time you manually edit an article on the review page, the system diffs your changes against the AI's original draft, extracts writing rules, and saves them to `data/agent_memory/style_learning_memory.md`. These rules are injected into every future writing call — the writer learns your preferences over time.

---

## 📤 Publishing Integrations

### WordPress
- Creates or updates posts via the WordPress REST API (`/wp-json/wp/v2/posts`).
- Sets Yoast SEO fields (`_yoast_wpseo_metadesc`, `_yoast_wpseo_title`) natively if the **Yoast Plugin** toggle is enabled in Settings.
- When Yoast is enabled, the Article JSON-LD schema block is omitted (Yoast handles it).
- Automatically generates a shorter punchy SEO title via LLM if the H1 + site title exceeds 60 characters.
- Uploads featured images to the WordPress media library.
- Auto-assigns the most relevant category using LLM classification.

### LinkedIn
- Posts the adapted article text using the LinkedIn Share API.
- Moves article URLs to the **first comment** (not embedded in the post body) per best practices.
- Ends the post body with "Discover more in comments".

### Brevo (Email Newsletter)
- Sends HTML newsletters to configured Brevo contact lists.
- Supports two modes: `update` (fresh article summary) and `summary` (weekly/monthly digest of published posts).

---

## 🔍 Advanced 5-Stage SEO Research & Discovery Pipeline

The system runs an automated **5-Stage SEO Discovery Pipeline** (`src/integrations/keywords.py`) to find high-potential, low-competition "Golden Ratio" keywords:

| Stage | What Happens |
|---|---|
| **1. Competitor Discovery** | Queries DataForSEO SERP API for the topic; extracts top 3 niche competitor domains (filters out Wikipedia, Amazon, etc.) |
| **2. Competitor Keyword Scrape** | Fetches ranked keywords for each competitor domain (positions 1–5); strips brand names and clinical noise |
| **3. Keyword Universe Expansion** | Aggregates top 30 candidates and queries DataForSEO Keyword Ideas API for long-tail variations |
| **4. Metric Sorting & Trend Verification** | Filters by KD ≤ 35 and Volume ≥ 300; validates 90-day trajectory via pytrends linear regression |
| **5. AI Brand-Relevance Filter** | Gemini acts as AI SEO Strategist, validates intent against brand pillars, selects the single best Golden Ratio keyword |

All research targets the **United Kingdom (UK)** market by default (configurable in `src/config.py`).

---

## 💬 Chat Agent

The Agent page (`/agent`) provides a natural-language interface to manage the content pipeline. The agent is powered by Gemini native function calling (or a custom Claude implementation) and can:

- **Create jobs** — "Schedule 3 articles about dementia care for next week"
- **List & manage jobs** — "What jobs are currently pending review?"
- **Generate 90-day plans** — "Create a Hub & Spoke content strategy around elderly home safety"
- **Approve & schedule clusters** — "Approve the latest plan and publish to WordPress and LinkedIn"

The agent's system prompt is dynamically built from your brand settings, keeping all suggestions aligned with your company context, ICP, and core pillars.

---

## 📄 Documentation

| Document | Description |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full system architecture: pipeline stages, scheduler, LLM router, agent, memory system, prompt locations |
| [`docs/ARCHITECTURE_ASSESSMENT.md`](docs/ARCHITECTURE_ASSESSMENT.md) | Critical assessment: pros/cons of current design, recommended improvements, framework evaluation, token cost optimisation strategies |

---

## 💾 Data Persistence

| Data | Location | Notes |
|---|---|---|
| **Database** | `./pgdata/` (bind mount) | All jobs, settings, cluster plans. Back this up. Delete to reset. |
| **Style Memory** | `./data/agent_memory/style_learning_memory.md` | Writer style rules learned from your edits |
| **Brand Cache** | `./data/agent_memory/brand_context_memory.json` | Cached brand settings for fast prompt construction |
| **Generated Images** | `./src/ui/static/generated_images/` | AI-generated article images |
| **Cluster Memory Logs** | `./data/agent_memory/plan_<id>_keywords.md` | Keyword discovery logs per cluster plan |

> ⚠️ The `./data/` directory is volume-mounted into the container. Ensure it persists across deployments.
