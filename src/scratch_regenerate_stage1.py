import asyncio
from src.database import AsyncSessionLocal
from src.models.job import ClusterPlan
from src.pipeline.cluster_orchestrator import run_cluster_plan_stage1

async def main():
    plan_id = "fc2411f2-a6a5-4d14-afad-5e91eb915b4b"
    async with AsyncSessionLocal() as session:
        plan = await session.get(ClusterPlan, plan_id)
        if plan:
            plan.status = "planning"
            plan.current_step = "keyword_research"
            plan.approved = False
            session.add(plan)
            await session.commit()
            print("Plan reset. Triggering Stage 1 keyword discovery...")
        else:
            print("Cluster Plan not found!")
            return
            
    # Run Stage 1 to regenerate keywords
    await run_cluster_plan_stage1(plan_id)
    print("Stage 1 completed. Please review the keywords in the dashboard.")

if __name__ == "__main__":
    asyncio.run(main())
