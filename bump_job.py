import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

from src.database import AsyncSessionLocal
from src.models.job import ArticleJob

async def main():
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, "a18b6240-0c34-4cda-8e40-5329787c1705")
        if not job:
            print("Job not found!")
            return
            
        print(f"Current status: {job.status}, Queue position: {job.queue_position}, Created At: {job.created_at}")
        
        # Make it the absolute oldest so it goes to the top
        job.created_at = datetime.utcnow() - timedelta(days=365)
        job.queue_position = 0 # Force it temporarily
        session.add(job)
        await session.commit()
        
        print("Successfully bumped job to the top of the queue (made it oldest in sequence).")

if __name__ == "__main__":
    asyncio.run(main())
