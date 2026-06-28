import asyncio
from sqlmodel import select
from src.database import AsyncSessionLocal
from src.models.job import ClusterPlan, ArticleJob, JobStatus
from src.pipeline.cluster_orchestrator import run_cluster_plan_stage2

async def main():
    plan_id = "fc2411f2-a6a5-4d14-afad-5e91eb915b4b"
    async with AsyncSessionLocal() as session:
        # 1. Delete all non-published jobs for this plan
        stmt = select(ArticleJob).where(ArticleJob.cluster_plan_id == plan_id)
        jobs = (await session.execute(stmt)).scalars().all()
        
        deleted_count = 0
        for job in jobs:
            if job.status != JobStatus.published:
                print(f"Deleting job {job.id} (status: {job.status})")
                await session.delete(job)
                deleted_count += 1
        
        await session.commit()
        print(f"Deleted {deleted_count} queued/pending jobs.")
        
        # 2. Reset the plan to generating_clusters state
        plan = await session.get(ClusterPlan, plan_id)
        if plan:
            plan.status = "generating_clusters"
            plan.current_step = "strategy_generation"
            plan.approved = False
            session.add(plan)
            await session.commit()
            print("Plan reset. Triggering Stage 2 strategy generation...")
        else:
            print("Cluster Plan not found!")
            return
            
    # 3. Run Stage 2 to regenerate the strategy matrix
    await run_cluster_plan_stage2(plan_id)
    print("Strategy generation completed successfully. Please review it in the dashboard.")

if __name__ == "__main__":
    asyncio.run(main())
