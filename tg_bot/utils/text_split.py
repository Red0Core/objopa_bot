"""Text splitting helpers extracted so media sending does not import AI SDKs."""

from telegramify_markdown import markdownify

from core.logger import logger


def split_text_smart(text: str, max_length: int) -> list[str]:
    if not text or max_length <= 0:
        return []

    if len(text) <= max_length:
        return [text]

    parts = []
    current_pos = 0
    text_length = len(text)

    while current_pos < text_length:
        end_pos = min(current_pos + max_length, text_length)

        if end_pos == text_length:
            parts.append(text[current_pos:])
            break

        fragment = text[current_pos:end_pos]
        split_position = _find_best_split_position(fragment)

        if split_position > 0:
            parts.append(text[current_pos : current_pos + split_position])
            current_pos += split_position
        else:
            parts.append(text[current_pos:end_pos])
            current_pos = end_pos

    return [part for part in parts if part.strip()]


def _find_best_split_position(text: str) -> int:
    newline_pos = text.rfind("\n")
    if newline_pos != -1:
        return newline_pos + 1

    for i in range(len(text) - 2, -1, -1):
        if text[i] == "." and i + 1 < len(text) and text[i + 1] == " ":
            return i + 2

    space_pos = text.rfind(" ")
    if space_pos != -1:
        return space_pos + 1

    return 0


def split_message_by_paragraphs(text: str, max_length: int = 4096) -> list[str]:
    if not text:
        return []

    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(paragraph) > max_length:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            chunks.extend(split_text_smart(paragraph, max_length))
            continue

        separator = "\n\n" if current_chunk else ""
        potential_chunk = current_chunk + separator + paragraph

        if len(potential_chunk) <= max_length:
            current_chunk = potential_chunk
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = paragraph

    if current_chunk:
        chunks.append(current_chunk.strip())

    return [chunk for chunk in chunks if chunk]


def get_gpt_formatted_chunks(text: str, max_length: int = 4096) -> list[str]:
    if not text:
        return []

    try:
        formatted_text = markdownify(text)
    except Exception as e:
        logger.warning(f"Ошибка форматирования markdown: {e}")
        formatted_text = text

    return split_message_by_paragraphs(formatted_text, max_length)
