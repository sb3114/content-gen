import asyncio
from dotenv import load_dotenv
load_dotenv()

from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus
from src.pipeline.orchestrator import run_pipeline

async def main():
    job_id = "a18b6240-0c34-4cda-8e40-5329787c1705"
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job:
            print("Job not found")
            return
        job.status = JobStatus.running
        job.queue_position = None
        session.add(job)
        await session.commit()
    
    print(f"Forcing run_pipeline for {job_id}...")
    await run_pipeline(job_id)
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
