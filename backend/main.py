import os

os.environ.setdefault("MALLOC_ARENA_MAX", "2")
os.environ.setdefault("PYTHONMALLOC", "malloc")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.routers.horoscope import router as horoscope_router
from backend.routers.markets import router as market_router
from backend.routers.notify import router as notify_router
from backend.routers.worker import router as worker_router
from core.memory import memory_report, start_memory_maintenance, trim_memory


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_memory_maintenance()
    try:
        yield
    finally:
        trim_memory()


app = FastAPI(lifespan=lifespan)
app.include_router(notify_router)
app.include_router(market_router)
app.include_router(horoscope_router)
app.include_router(worker_router)


@app.get("/health/memory")
async def health_memory():
    return {"status": "ok", "report": memory_report()}
