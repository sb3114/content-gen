import asyncio
from sqlmodel import select
from src.database import AsyncSessionLocal
from src.models.job import ClusterPlan

async def check():
    async with AsyncSessionLocal() as session:
        plan = await session.get(ClusterPlan, "fc2411f2-a6a5-4d14-afad-5e91eb915b4b")
        if plan and plan.keywords:
            statuses = [kw.get("status") for kw in plan.keywords]
            print(f"Total keywords: {len(statuses)}")
            print(f"Approved: {statuses.count('approved')}")
            print(f"Pending: {statuses.count('pending')}")
            print(f"Deleted: {statuses.count('deleted')}")
            print(f"None: {statuses.count(None)}")
        else:
            print("No keywords found")

asyncio.run(check())
