from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from src.config import settings
# Import all models so their metadata is registered for create_all
from src.models.settings import CompanySettings
from src.models.job import ArticleJob  # noqa: F401
from src.models.chat import AgentConversation, AgentMessage  # noqa: F401

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=30,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Ensure new columns exist on pre-existing database tables
        from sqlalchemy import text

        # Existing columns (already shipped)
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS dataforseo_login VARCHAR;"))
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS dataforseo_password VARCHAR;"))

        # Sequential queue + keyword gate (this release)
        await conn.execute(text("ALTER TABLE article_jobs ADD COLUMN IF NOT EXISTS queue_position INTEGER;"))
        await conn.execute(text("ALTER TABLE article_jobs ADD COLUMN IF NOT EXISTS auto_approve BOOLEAN NOT NULL DEFAULT FALSE;"))
        await conn.execute(text("ALTER TABLE article_jobs ADD COLUMN IF NOT EXISTS confirmed_keyword VARCHAR;"))
        await conn.execute(text("ALTER TABLE article_jobs ADD COLUMN IF NOT EXISTS keyword_review_data JSON;"))

        # WordPress on-page SEO author fields
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS wp_author_id INTEGER;"))
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS wp_author_name VARCHAR;"))

        # LLM Orchestration fields
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS llm_provider VARCHAR NOT NULL DEFAULT 'gemini';"))
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS claude_setup_token VARCHAR;"))
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS allow_fallback_to_haiku BOOLEAN NOT NULL DEFAULT TRUE;"))
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS rate_limit_banner VARCHAR;"))
        await conn.execute(text("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS rate_limit_until TIMESTAMP;"))

    try:
        # Alter ENUM types outside transaction blocks (PostgreSQL requires this)
        async with engine.connect() as conn:
            conn_auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn_auto.execute(
                text("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'queued';")
            )
            await conn_auto.execute(
                text("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'resuming';")
            )
    except Exception as e:
        import logging
        logging.warning(f"Failed to add enum values (might already exist): {e}")
