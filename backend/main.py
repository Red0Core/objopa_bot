from fastapi import FastAPI

from backend.routers.horoscope import router as horoscope_router
from backend.routers.markets import router as market_router
from backend.routers.notify import router as notify_router
from backend.routers.worker import router as worker_router

app = FastAPI()
app.include_router(notify_router)
app.include_router(market_router)
app.include_router(horoscope_router)
app.include_router(worker_router)
