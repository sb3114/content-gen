import asyncio
from sqlmodel import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from src.config import settings
from src.models.job import ArticleJob

async def check():
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        jobs = (await session.exec(select(ArticleJob).where(ArticleJob.cluster_plan_id == None))).all()
        print(f"Standalone jobs with == None: {len(jobs)}")
        
        jobs2 = (await session.exec(select(ArticleJob).where(ArticleJob.cluster_plan_id.is_(None)))).all()
        print(f"Standalone jobs with .is_(None): {len(jobs2)}")
        
asyncio.run(check())
