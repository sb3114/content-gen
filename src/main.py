from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.database import init_db
from src.api.jobs import router as jobs_router
from src.api.auth import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Content Engine",
    description="AI-powered article generation and publishing",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")
app.include_router(jobs_router)
app.include_router(auth_router)
