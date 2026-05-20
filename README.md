# Content Engine

AI-powered article generation and publishing platform built with FastAPI, PostgreSQL, and Google Gemini.

## 🏗️ Architecture & Codebase

The application is structured as a modular asynchronous API:

*   **`src/api/`**: Contains the FastAPI route handlers.
    *   `jobs.py`: Endpoints to create, track, and manage AI content generation jobs.
    *   `auth.py`: Endpoints for managing authentication (e.g., LinkedIn OAuth, WordPress).
*   **`src/pipeline/`**: The core AI orchestration engine.
    *   `orchestrator.py`: Manages the overall workflow and state transitions of a content job.
    *   `research.py`, `planning.py`, `writing.py`, `refinement.py`: Specific stages of the AI content generation process.
    *   `summarize.py`: Automatically summarizes context (e.g., company context) for the AI memory.
    *   `linkedin_adapt.py`: Transforms the generated content into LinkedIn-ready formats.
*   **`src/integrations/`**: Connectors for external services.
    *   `keywords.py`: Logic for researching topics via Google Trends and SEO APIs.
    *   `wordpress.py`, `linkedin.py`, `brevo.py`: API clients for publishing platforms.
*   **`src/models/`**: Database models (SQLAlchemy/SQLModel) for `Job` and `Settings`.
*   **`src/database.py`**: PostgreSQL database connection and session management using `asyncpg`.
*   **`src/ui/`**: Frontend components, including `static` assets (CSS/JS) and `templates`.

## 🔍 Advanced 5-Stage SEO Research & Discovery Pipeline

The system contains an automated, touchless **5-Stage SEO Discovery and Trend Verification Pipeline** (`src/integrations/keywords.py`) designed to identify high-potential, brand-aligned, and low-competition "Golden Ratio" keywords in the target market.

### 🌐 Dynamic Geography & Language Targeting
By default, all keyword metrics, competitor discoveries, and long-tail ideas are dynamically configured in [config.py](file:///home/shirish/Documents/development/content-creator/content-creator/src/config.py) to target specific local markets:
- **Default Target**: **United Kingdom (UK)** (Location Code: `2826`, Language: `en`).
- Fully customizable to prioritize localized search volumes and intent rather than generic international data.

---

### 🚀 How the 5-Stage Pipeline Works:

#### 1️⃣ Stage 1: Competitor Discovery & Niche Filtering
- Takes your initial topic string and queries the **DataForSEO Google Organic Live SERP API** (`v3/serp/google/organic/live/advanced`).
- Extracts root domains of the top 3 ranking organic sites.
- **Niche Competitor Domain Filter**: Programmatically filters out giant generic authorities (like `amazon.com`, `nytimes.com`, `wikipedia.org`, `nih.gov`, `youtube.com`) to isolate **highly specialized niche blogs, local platforms, and domain competitors** (e.g. `smartcaregiver.com`, `agespace.org`), keeping our source keywords highly relevant.

#### 2️⃣ Stage 2: Competitor Keyword Scrape & Brand Cleanse
- Queries the **DataForSEO Ranked Keywords API** (`v3/dataforseo_labs/google/ranked_keywords/live`) for each discovered competitor.
- Restricts scraping to high-performing pages (positions 1-5).
- **Brand Cleanse & Noise Scrubbing**: Programmatically strips out phrases containing competitor names (e.g., if scraping `aplaceformom.com`, brand phrases like `"a place for mom senior advisors"` are omitted).
- **Clinical & Character Filter**: Scrubs out decimal coordinates, drug/clinical percent codes, decimals, and special character strings (e.g. `"0.9 sodium chloride"`, `"027/nap1/bi"`) that cause DataForSEO validation errors and system crashes.

#### 3️⃣ Stage 3: Keyword Universe Expansion (Granular Long-Tails)
- Aggregates the remaining competitor terms and slices them to the top 30 candidates.
- Queries the **DataForSEO Keyword Ideas API** (`v3/dataforseo_labs/google/keyword_ideas/live`) to generate highly granular long-tail variations.
- **Single POST Call Optimization**: Submits candidate seeds in a single nested batch request to keep processing highly cost-effective and hyper-fast.

#### 4️⃣ Stage 4: Metric Sorting & Trajectory Trend Verification
- Filters the master long-tail list using strict bounds: **Keyword Difficulty (KD) <= 35** and **Search Volume >= 300**.
- Integrates parallel **Google Trends (pytrends)** batch lookups (groups of 5) to calculate a **90-day trajectory slope** of interest over time using linear regression.
- Discards declining trends (`slope < -0.5`), keeping only growing or stable queries.

#### 5️⃣ Stage 5: AI Brand-Relevance Filter & Sequential Scheduling
- Passes surviving candidates to Gemini along with **BondNow's brand description, ICPs, and core marketing pillars** loaded dynamically from settings.
- Gemini acts as our **AI Chief SEO Strategist**, analyzing search intent against company values, discarding off-topic candidate queries, and choosing the single best **"Golden Ratio" Focus Keyword**.
- Queries the calendar state via `get_next_open_slot` to determine the latest scheduled post, and schedules the new job sequentially at **9:00 AM** on the next available open day.

---

### ✏️ Post-Schedule Editing & Target Recalculation

Even after a task is approved or scheduled, you retain full editorial control over your content:
- **Interactive Editing**: The article title, body, LinkedIn draft, and newsletter fields remain fully editable, featuring a persistent `💾 Save Content Changes` button.
- **Target-based Recalculation Pipeline**: Modifying publish targets in the top Edit Panel dynamically recalculates assets:
  - **Added Targets**: Checking a new publish target (e.g., checking LinkedIn or Newsletter) spawns an **asynchronous LLM adaptation background task** to immediately generate high-quality tailored drafts based on the active article content.
  - **Removed Targets**: Unchecking a publish target instantly cleanses and purges its draft content from the database.

---

### 📊 Integrated SEO Stats & Metrics Dashboard (UI/UX)
All live SEO metrics are persistently available across all states (Scheduled, Published, Approved) in a premium collapsable details panel:
- **Focus Golden Ratio Keyword Banner**: Highlighted in beautiful neon HSL gradients with UK targeting indicators, monthly volume metrics, trend trajectory graphs, and the complete AI Chief Strategist Rationale.
- **Discovered Competitors**: Links directly to the organic sources we scraped.
- **Top Candidates Table**: Custom-styled rows displaying candidates that survived threshold bounds with color-coded difficulty badges.

## 🚀 How to Start the App

The application is containerized using Docker and Docker Compose, making it easy to run.

### Prerequisites

1.  [Docker](https://docs.docker.com/get-docker/) installed.
2.  [Docker Compose](https://docs.docker.com/compose/install/) installed.

### Setup

1.  **Environment Variables (Non-Sensitive):**
    Copy the example `.env` file to set up basic configuration.
    ```bash
    cp .env.example .env
    ```

2.  **Secrets:**
    Sensitive keys like `GEMINI_API_KEY`, `POSTGRES_PASSWORD`, etc., should not be saved in the `.env` file. You need to export them to your shell environment before starting Docker Compose. 
    You can create a `scripts/set-secrets.sh` (using `.example` if available) and source it:
    ```bash
    source scripts/set-secrets.sh
    ```
    Alternatively, export them manually:
    ```bash
    export POSTGRES_PASSWORD="your-strong-password"
    export GEMINI_API_KEY="your-gemini-api-key"
    export SECRET_KEY="your-secret-key"
    # Export other required variables (WordPress, LinkedIn) as needed
    ```

### Running the Application

Once your environment variables are configured, start the application and database using:

```bash
docker compose up --build
```

*(Add `-d` to run in detached mode).*

*   **API / App Interface:** `http://localhost:8080`
*   **PostgreSQL Database:** Running on port `5433` on the host (mapped from `5432` in the container).

## 💾 Data Persistence

To ensure that your PostgreSQL database data is not lost when containers are restarted or removed, the database uses a **local bind mount**.

*   Database data is persisted in the `./pgdata` directory at the root of the project.
*   You can back up this folder to save all database state. 
*   If you delete the `./pgdata` folder, your database will be reset to a fresh state.
