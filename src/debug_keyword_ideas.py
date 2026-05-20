import asyncio
import base64
import httpx
import sys
import json

sys.path.append(".")
from src.database import AsyncSessionLocal
from src.models.settings import CompanySettings


async def main():
    async with AsyncSessionLocal() as session:
        settings_obj = await session.get(CompanySettings, 1)
        login = settings_obj.dataforseo_login
        password = settings_obj.dataforseo_password
        creds = base64.b64encode(f"{login}:{password}".encode()).decode()

    seeds = ["bed sensor", "chair alarm sensor pad", "elderly care monitoring system", "bed alarm systems"]
    payload = [
        {
            "keywords": seeds,
            "location_code": 2840,
            "language_name": "English",
            "limit": 10
        }
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.dataforseo.com/v3/dataforseo_labs/google/keyword_ideas/live",
            json=payload,
            headers={"Authorization": f"Basic {creds}"},
        )
        print("Status code:", resp.status_code)
        data = resp.json()
        print("Response status_code:", data.get("status_code"))
        print("Response status_message:", data.get("status_message"))
        
        tasks = data.get("tasks", [])
        if tasks:
            print("Task 0 status_code:", tasks[0].get("status_code"))
            print("Task 0 status_message:", tasks[0].get("status_message"))
            result = tasks[0].get("result", [{}])[0]
            items = result.get("items", [])
            print("Total items:", len(items))
            if items:
                print("First item keys:", list(items[0].keys()))
                print("First item sample:")
                print(json.dumps(items[0], indent=2))
            else:
                print("No items in result. Full response:")
                print(json.dumps(data, indent=2))
        else:
            print("No tasks in response:", data)


if __name__ == "__main__":
    asyncio.run(main())
