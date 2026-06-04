import asyncio
from google import genai
from google.genai import types
from src.config import settings

client = genai.Client(api_key=settings.gemini_api_key)
async def main():
    try:
        cache = client.caches.create(
            model="gemini-2.5-pro",
            config=types.CreateCachedContentConfig(
                system_instruction="Hello " * 500,
                ttl="3600s"
            )
        )
        print("Success:", cache.name)
    except Exception as e:
        print("Error:", repr(e))

asyncio.run(main())
