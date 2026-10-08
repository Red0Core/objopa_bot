"""Send AI Markdown as rich content, with a legacy fallback for API limitations."""

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNotFound
from aiogram.types import InputRichMessage, Message

from core.logger import logger
from tg_bot.services.gpt import get_gpt_formatted_chunks

RICH_MAX_CHARS = 32768


def _rich_fits(text: str) -> bool:
    # Conservative preflight; Telegram remains authoritative for parsed blocks and nesting.
    lines = text.splitlines()
    return (
        len(text) <= RICH_MAX_CHARS
        and len(lines) <= 500
        and all(len(line) - len(line.lstrip(" >")) <= 16 for line in lines)
        and all(line.count("|") <= 21 for line in lines if line.lstrip().startswith("|"))
    )


async def _remove_placeholder(placeholder: Message | None) -> None:
    if placeholder is not None:
        try:
            await placeholder.delete()
        except TelegramAPIError:
            logger.debug("AI placeholder could not be deleted")


async def send_ai_response(message: Message, placeholder: Message | None, response_text: str) -> None:
    """Only definitive API rejections trigger retries, never ambiguous network failures."""
    if not response_text.strip():
        response_text = "Модель вернула пустой ответ."
    if _rich_fits(response_text):
        rich = InputRichMessage(markdown=response_text)
        try:
            if placeholder is not None:
                try:
                    await placeholder.edit_text(rich_message=rich)
                    return
                except TelegramBadRequest as exc:
                    reason = exc.message.lower()
                    if "message is not modified" in reason:
                        return
                    if "message to edit not found" not in reason and "message can't be edited" not in reason:
                        raise
            await message.answer_rich(rich, reply_parameters=message.as_reply_parameters())
        except (TelegramBadRequest, TelegramNotFound, NotImplementedError):
            logger.info("Rich AI response rejected; using MarkdownV2 fallback")
        else:
            await _remove_placeholder(placeholder)
            return

    # Long/block-heavy content uses the established safe legacy splitter.
    chunks = get_gpt_formatted_chunks(response_text)
    if not chunks:
        await _send_legacy(message, placeholder, response_text, plain=True)
    else:
        await _send_legacy(message, placeholder, response_text, chunks=chunks)


async def _send_legacy(
    message: Message,
    placeholder: Message | None,
    original: str,
    *,
    chunks: list[str] | None = None,
    plain: bool = False,
) -> None:
    # 2000 Unicode code points fit the ordinary 4096 UTF-16-unit limit, including emoji.
    if chunks is None:
        chunks = [original[i : i + 2000] for i in range(0, len(original), 2000)]
    previous = message
    for index, chunk in enumerate(chunks):
        mode = None if plain else "MarkdownV2"
        try:
            if index == 0 and placeholder is not None:
                try:
                    await placeholder.edit_text(chunk, parse_mode=mode)
                    previous = placeholder
                    continue
                except TelegramBadRequest as exc:
                    reason = exc.message.lower()
                    if "message is not modified" in reason:
                        previous = placeholder
                        continue
                    if "message to edit not found" not in reason and "message can't be edited" not in reason:
                        raise
            previous = await previous.reply(chunk, parse_mode=mode)
        except TelegramBadRequest as exc:
            reason = exc.message.lower()
            if not plain and ("parse" in reason or "entity" in reason or "too long" in reason):
                if index == 0:
                    # Nothing has been delivered: preserve the original Markdown literally.
                    await _send_legacy(message, placeholder, original, plain=True)
                    return
                for start in range(0, len(chunk), 2000):
                    previous = await previous.reply(chunk[start : start + 2000], parse_mode=None)
            else:
                raise
        if index == 0:
            await _remove_placeholder(placeholder)
