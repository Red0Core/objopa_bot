import re


def extract_current_question(text: str) -> str:
    """Route the new question rather than the quoted reply context assembled by gpt_router."""
    if "Контекст из предыдущего сообщения:\n---\n" in text:
        _, separator, question = text.rpartition("\n---\n\n")
        if separator:
            return question
    return text


def requests_sources(text: str) -> bool:
    return bool(
        re.search(
            r"(?:дай|покажи|скинь|приведи|укажи|добавь).{0,25}(?:источник\w*|ссылк\w*|url)|"
            r"(?:give|show|provide|include).{0,25}(?:sources?|links?|urls?)",
            text,
            re.IGNORECASE,
        )
    )
