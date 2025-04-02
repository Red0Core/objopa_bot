from fastapi import FastAPI
from routers.notify import router as notify_router

app = FastAPI()
app.include_router(notify_router)
