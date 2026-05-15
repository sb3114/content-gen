from contextlib import asynccontextmanager
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.database import init_db
from src.api.jobs import router as jobs_router
from src.api.auth import router as auth_router
from src.api.calendar import router as calendar_router
from src.api.agent import router as agent_router
from src.pipeline.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Content Engine",
    description="AI-powered article generation and publishing",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")
app.include_router(jobs_router)
app.include_router(auth_router)
app.include_router(calendar_router)
app.include_router(agent_router)
