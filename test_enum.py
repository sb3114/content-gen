import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def test():
    engine = create_async_engine("postgresql+asyncpg://postgres:postgres@localhost:5433/content_creator")
    try:
        async with engine.connect() as conn:
            # Setting isolation level to AUTOCOMMIT
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'queued'"))
            await conn.execute(text("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'resuming'"))
            print("Enum added successfully")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
