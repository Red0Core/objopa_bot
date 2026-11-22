"""Recover readable Telegram reply context, including rich blocks."""

import asyncio
from typing import Any

from aiogram.types import Message

from core.logger import logger
from core.redis_client import get_redis

TTL = 86400
MAX_BYTES = 131072


def render(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(render(item) for item in value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if not isinstance(value, dict):
        return ""
    kind = value.get("type", "")
    text = render(value.get("text", ""))
    blocks = "\n\n".join(render(block) for block in value.get("blocks", []))
    if kind == "url":
        return f"[{text}]({value['url']})"
    if kind == "heading":
        return "#" * min(6, max(1, value.get("size", 1))) + " " + text
    if kind == "pre":
        return f"```{value.get('language', '')}\n{text}\n```"
    if kind == "list":
        return "\n".join(f"{item.get('label') or '-'} {render(item)}" for item in value.get("items", []))
    if kind == "table":
        rows = ["| " + " | ".join(render(cell) for cell in row) + " |" for row in value.get("cells", [])]
        return "\n".join(rows)
    if kind in {"blockquote", "expandable_blockquote", "pullquote"}:
        return "\n".join("> " + line for line in blocks.splitlines())
    return text or blocks or render(value.get("caption", ""))


def extract_message_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return render(message.rich_message) if message.rich_message else ""


async def remember_response(message: Message, text: str) -> None:
    if not isinstance(message, Message) or len(text.encode("utf-8")) > MAX_BYTES:
        return
    try:
        async with asyncio.timeout(1):
            redis = await get_redis()
            await redis.set(f"ai:reply:{message.chat.id}:{message.message_id}", text, ex=TTL)
    except Exception:
        logger.warning("Reply context cache unavailable")


async def extract_reply_context(message: Message) -> str:
    if message.quote and message.quote.text:
        return message.quote.text
    source = message.reply_to_message
    if not source:
        return ""
    text = extract_message_text(source)
    if text:
        return text
    try:
        async with asyncio.timeout(1):
            redis = await get_redis()
            text = await redis.get(f"ai:reply:{source.chat.id}:{source.message_id}")
            return text if isinstance(text, str) else ""
    except Exception:
        logger.warning("Reply context cache unavailable")
        return ""
