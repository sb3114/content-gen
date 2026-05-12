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
*   **`src/models/`**: Database models (SQLAlchemy/SQLModel) for `Job` and `Settings`.
*   **`src/database.py`**: PostgreSQL database connection and session management using `asyncpg`.
*   **`src/ui/`**: Frontend components, including `static` assets (CSS/JS) and `templates`.

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
