from fastapi import APIRouter, Request
import redis.asyncio as redis
import os, ujson
from dotenv import load_dotenv

load_dotenv()

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)
router = APIRouter()

@router.post("/notify")
async def push_notification(request: Request):
    data = await request.json()
    await r.lpush("notifications", ujson.dumps(data))
    return {"status": "queued"}
