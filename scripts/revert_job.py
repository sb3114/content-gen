import asyncio
from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus
from sqlmodel import select

async def main():
    job_id = "7a3b2666-3ff6-480a-b9e2-db66f2c8e3c2"
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job:
            print(f"Job {job_id} not found.")
            return

        print(f"Reverting Job: {job.id}")
        print(f"Current Status: {job.status}")

        # Update status
        job.status = JobStatus.pending_review
        job.current_step = None

        # Append "Discover more in comments" to reviewed_linkedin or linkedin_post if not present
        li_text = job.reviewed_linkedin or job.linkedin_post or ""
        if li_text:
            lower_text = li_text.lower()
            if "discover more in comments" not in lower_text and "discover more in commen" not in lower_text:
                hashtag_idx = li_text.find("#")
                if hashtag_idx != -1:
                    before = li_text[:hashtag_idx].rstrip()
                    after = li_text[hashtag_idx:]
                    new_text = f"{before}\n\nDiscover more in comments\n\n{after}"
                else:
                    new_text = f"{li_text.rstrip()}\n\nDiscover more in comments"
                
                if job.reviewed_linkedin:
                    job.reviewed_linkedin = new_text
                if job.linkedin_post:
                    job.linkedin_post = new_text
                print("Appended 'Discover more in comments' to LinkedIn post body.")

        session.add(job)
        await session.commit()
        print("Job reverted successfully to pending_review.")

if __name__ == '__main__':
    asyncio.run(main())
