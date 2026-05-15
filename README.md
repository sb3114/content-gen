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

## 🔍 Keyword Research Integration

The system includes a sophisticated Keyword Researcher (`src/integrations/keywords.py`) that automates the discovery of search intent and competition data. 

### How it works:
When a new job is started, the engine performs the following in parallel:

1.  **Google Trends (pytrends)**:
    *   Fetches **Interest Over Time** (last 12 months) for your seed keywords.
    *   Extracts **Related Queries** (Top 20) to expand topical coverage and find niche "hooks."
2.  **Search Metrics (DataForSEO)**:
    *   *Optional*: Activated if DataForSEO credentials are provided in Settings.
    *   Fetches real-time **Search Volume**, **Competition levels**, and **CPC** (Cost Per Click).
    *   Targeting: **United States (English)** by default.

### Benefits:
- **Asynchronous Execution**: Both research tasks run simultaneously via `asyncio`, minimizing the "cold start" time for new jobs.
- **Data-Driven Writing**: This researched data (volumes + related queries) is injected directly into the Gemini Planning prompt.

### 💰 Keyword Selection & "Affordability"

A common question is how the system chooses "affordable" keywords (those with high potential but low competition). 

**How it is done today:**
1.  **Metric Fetching**: The `KeywordResearcher` retrieves the `competition` index from DataForSEO (a value from 0 to 1.0, where 1.0 is highest competition).
2.  **Context Injection**: This raw data—`Keyword`, `Search Volume`, and `Competition`—is passed in its entirety to the **Gemini Planning Agent**.
3.  **AI Decision Making**: The Gemini prompt explicitly defines the model as an **"Expert SEO strategist."** Gemini uses its internal reasoning to balance high search volume against low competition scores to select the `focus_keyword` and `secondary_keywords`.

*Note: There is currently no hard-coded "filter" (e.g., skip all keywords > 0.5 competition). The selection is dynamic and based on the AI's topical understanding.*

### 🛡️ Token Optimization Algorithm

To maximize ROI and prevent wasting expensive AI tokens on topics that are too competitive or have low search intent, the system enforces a strict **Viability Guardrail** after the research phase:

**The Rule:**
> *If no keyword exists where **KD <= 30** AND **Search Volume >= 500**, the article generation is aborted.*

- **KD Mapping**: Since DataForSEO's `competition` index is 0.0–1.0, we use `competition * 100` as a proxy for Keyword Difficulty (KD).
- **Automation**: If the threshold is not met, the job status is automatically set to `failed` with a clear message, saving you from spending tokens on the Planning and Writing phases for a non-viable topic.
- **Exemptions**: This check is automatically bypassed for **Newsletter Summaries**, as they are based on existing content rather than SEO targeting.

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
