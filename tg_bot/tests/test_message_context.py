from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from tg_bot.services import message_context as context


def message(**kwargs):
    return Message(message_id=1, date=0, chat={"id": 1, "type": "private"}, **kwargs)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"text": "plain"}, "plain"),
        ({"caption": "caption"}, "caption"),
        (
            {"rich_message": {"blocks": [{"type": "paragraph", "text": "one"}, {"type": "paragraph", "text": "two"}]}},
            "one\n\ntwo",
        ),
        (
            {"rich_message": {"blocks": [{"type": "pre", "language": "python", "text": "print(1)"}]}},
            "```python\nprint(1)\n```",
        ),
    ],
)
@pytest.mark.asyncio
async def test_reply(payload, expected):
    source = message(**payload)
    assert await context.extract_reply_context(message(reply_to_message=source)) == expected


@pytest.mark.asyncio
async def test_quote_preferred():
    assert (
        await context.extract_reply_context(
            message(quote={"text": "selected", "position": 0}, reply_to_message=message(text="whole"))
        )
        == "selected"
    )


@pytest.mark.asyncio
async def test_cache_restore_and_bounds(monkeypatch):
    redis = AsyncMock()
    redis.get.return_value = "Original markdown"
    monkeypatch.setattr(context, "get_redis", AsyncMock(return_value=redis))
    await context.remember_response(message(), "Original markdown")
    redis.set.assert_awaited_once_with("ai:reply:1:1", "Original markdown", ex=context.TTL)
    assert await context.extract_reply_context(message(reply_to_message=message())) == "Original markdown"
    await context.remember_response(message(), "x" * (context.MAX_BYTES + 1))
    assert redis.set.await_count == 1
    assert await context.extract_reply_context(message()) == ""


def test_list_table_and_link():
    data = {
        "blocks": [
            {"type": "list", "items": [{"label": "1.", "blocks": [{"type": "paragraph", "text": "Item"}]}]},
            {"type": "table", "cells": [[{"text": "A"}, {"text": "B"}]]},
            {"type": "paragraph", "text": {"type": "url", "text": "Site", "url": "https://example.com"}},
        ]
    }
    text = context.render(data)
    assert "1. Item" in text and "| A | B |" in text and "[Site](https://example.com)" in text
