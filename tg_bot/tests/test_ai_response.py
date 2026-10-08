from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText

from tg_bot.services import ai_response


def bad(reason):
    return TelegramBadRequest(method=EditMessageText(chat_id=1, message_id=1, text="test"), message=reason)


def messages():
    message = MagicMock()
    placeholder = MagicMock()
    message.answer_rich = AsyncMock(return_value=message)
    message.reply = AsyncMock(return_value=message)
    placeholder.edit_text = AsyncMock(return_value=placeholder)
    placeholder.reply = AsyncMock(return_value=placeholder)
    placeholder.delete = AsyncMock()
    return message, placeholder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Привет!",
        "# Статья\n\n## Раздел\nТекст **статьи**.",
        "| Компонент | Статус |\n|---|---|\n| Backend | ✅ Готов |",
        "```python\nprint('ok')\n```",
        "abc " * 2000,
    ],
)
async def test_raw_markdown_is_edited_as_one_rich_message_without_legacy_conversion(monkeypatch, text):
    message, placeholder = messages()
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", lambda text: pytest.fail("Rich must stay raw"))
    await ai_response.send_ai_response(message, placeholder, text)
    placeholder.edit_text.assert_awaited_once()
    assert placeholder.edit_text.call_args.kwargs["rich_message"].markdown == text
    assert "parse_mode" not in placeholder.edit_text.call_args.kwargs
    message.answer_rich.assert_not_awaited()
    placeholder.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_rich_format_error_uses_legacy_once(monkeypatch):
    message, placeholder = messages()
    placeholder.edit_text.side_effect = [bad("Can't parse rich message"), placeholder]
    formatter = MagicMock(return_value=["Legacy text"])
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", formatter)
    await ai_response.send_ai_response(message, placeholder, "**Raw text**")
    formatter.assert_called_once_with("**Raw text**")
    assert placeholder.edit_text.await_count == 2
    assert placeholder.edit_text.call_args.args == ("Legacy text",)
    message.answer_rich.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_uneditable_placeholder_sends_rich_once_then_deletes():
    message, placeholder = messages()
    placeholder.edit_text.side_effect = bad("message can't be edited")
    await ai_response.send_ai_response(message, placeholder, "# Heading")
    message.answer_rich.assert_awaited_once()
    assert message.answer_rich.call_args.args[0].markdown == "# Heading"
    placeholder.delete.assert_awaited_once()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_api_method_falls_back(monkeypatch):
    message, placeholder = messages()
    message.answer_rich.side_effect = bad("method not found")
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", lambda text: ["Fallback"])
    await ai_response.send_ai_response(message, None, "Text")
    message.answer_rich.assert_awaited_once()
    message.reply.assert_awaited_once_with("Fallback", parse_mode="MarkdownV2")


@pytest.mark.asyncio
async def test_network_failure_does_not_retry_or_duplicate():
    message, placeholder = messages()
    placeholder.edit_text.side_effect = TelegramNetworkError(
        method=EditMessageText(chat_id=1, message_id=1, text="test"), message="timeout"
    )
    with pytest.raises(TelegramNetworkError):
        await ai_response.send_ai_response(message, placeholder, "Text")
    message.answer_rich.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_answer_falls_back_without_loss(monkeypatch):
    message, placeholder = messages()
    text = "abc " * 10000
    chunks = [text[i : i + 2000] for i in range(0, len(text), 2000)]
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", lambda text: chunks)
    await ai_response.send_ai_response(message, placeholder, text)
    assert "rich_message" not in placeholder.edit_text.call_args.kwargs
    sent = [placeholder.edit_text.call_args.args[0], *[call.args[0] for call in placeholder.reply.call_args_list]]
    assert "".join(sent) == text
    message.answer_rich.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_parse_failure_preserves_original_text_as_plain(monkeypatch):
    message, placeholder = messages()
    placeholder.edit_text.side_effect = [bad("rich invalid"), bad("can't parse entities"), placeholder]
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", lambda text: ["converted"])
    text = "**original**\n```python\nprint('x')\n```"
    await ai_response.send_ai_response(message, placeholder, text)
    assert placeholder.edit_text.call_args.args[0] == text
    assert placeholder.edit_text.call_args.kwargs["parse_mode"] is None
    message.reply.assert_not_awaited()


@pytest.mark.parametrize("text", ["- item\n" * 501, ">" * 17 + "nested", "|" * 22])
def test_rich_preflight_falls_back_for_conservative_block_depth_and_column_limits(text):
    assert not ai_response._rich_fits(text)


@pytest.mark.asyncio
async def test_later_chunk_failure_does_not_resend_first_chunk(monkeypatch):
    message, placeholder = messages()
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", lambda text: ["first", "second"])
    placeholder.reply.side_effect = [bad("can't parse entities"), placeholder]
    await ai_response.send_ai_response(message, placeholder, "x" * 33000)
    placeholder.edit_text.assert_awaited_once_with("first", parse_mode="MarkdownV2")
    assert placeholder.reply.call_args.args == ("second",)
    assert placeholder.reply.call_args.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_long_unicode_rejected_by_legacy_preserves_original(monkeypatch):
    message, placeholder = messages()
    original = "😀" * 33000
    placeholder.edit_text.side_effect = [bad("message is too long"), placeholder]
    monkeypatch.setattr(ai_response, "get_gpt_formatted_chunks", lambda text: [text])
    await ai_response.send_ai_response(message, placeholder, original)
    sent = [placeholder.edit_text.call_args.args[0], *[call.args[0] for call in placeholder.reply.call_args_list]]
    assert "".join(sent) == original
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in sent)
