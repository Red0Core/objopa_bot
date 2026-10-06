import re

_FRESHNESS = re.compile(
    r"\b(?:последн\w*|новейш\w*|актуальн\w*|текущ\w*|свеж\w*|недавн\w*|сегодня|сейчас|"
    r"ласт\w*|щас|ща|новая|новый|новое|новые|latest|newest|current|today|now|recent\w*|presently)\b",
    re.IGNORECASE,
)
_LATEST = re.compile(r"\b(?:новейш\w*|latest|newest)\b", re.IGNORECASE)
_DYNAMIC_TOPIC = re.compile(
    r"\b(?:модел\w*|верси\w*|релиз\w*|новост\w*|президент\w*|премьер\w*|министр\w*|"
    r"руководител\w*|директор\w*|курс\w*|цен[аыуе]\w*|стоимост\w*|лимит\w*|квот\w*|тариф\w*|"
    r"характеристик\w*|расписани\w*|рейтинг\w*|результат\w*|закон\w*|правил\w*|наличи\w*|"
    r"доступност\w*|model\w*|version\w*|release\w*|news|president|ceo|executive\w*|"
    r"price\w*|pricing|rate\w*|quota\w*|limit\w*|spec\w*|schedule\w*|ranking\w*|score\w*|"
    r"result\w*|law\w*|regulation\w*|polic\w*|availab\w*|market\w*|api)\b",
    re.IGNORECASE,
)
_TECH = re.compile(r"\b(?:gpt\w*|gemini|openai|claude|python|fastapi|iphone|android|cpu|gpu)\b", re.IGNORECASE)
_TRANSFORM = re.compile(r"\b(?:переведи|перепиши|отредактируй|translate|rewrite|rephrase)\b", re.IGNORECASE)
_CODE = re.compile(r"(?:напиши|write|implement).{0,35}(?:функци\w*|код|скрипт|function|code|script)", re.IGNORECASE)
_RELEASE_QUESTION = re.compile(
    r"(?:когда.{0,30}(?:выш\w*|выход\w*|выпущ\w*)|when.{0,35}(?:release\w*|launch\w*))", re.IGNORECASE
)
_PRODUCT = re.compile(r"\b(?:iphone\s*\d+|pixel\s*\d+|galaxy\s*[saz]?\d+|rtx\s*\d+|ryzen\s*\d+)\b", re.IGNORECASE)


def normalize_search_entities(text: str) -> str:
    for pattern, replacement in (
        (r"\bгпт\b", "GPT"),
        (r"\bопенаи\b", "OpenAI"),
        (r"\bпитон\w*\b", "Python"),
        (r"\bайфон\w*\b", "iPhone"),
        (r"\b(?:биток|битка|битку|битком|битке)\b", "Bitcoin"),
        (r"\bбтк\b", "BTC"),
    ):
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return " ".join(text.split())


def official_domains_for_query(text: str) -> tuple[str, ...]:
    text = normalize_search_entities(text)
    domains = []
    for pattern, domain in (
        (r"\b(?:gpt\w*|гпт|openai)\b", "openai.com"),
        (r"\b(?:python|питон\w*)\b", "python.org"),
        (r"\b(?:apple|iphone|айфон\w*)\b", "apple.com"),
        (r"\b(?:google|gemini)\b", "ai.google.dev"),
        (r"\b(?:google|gemini)\b", "developers.google.com"),
        (r"\b(?:google|gemini)\b", "blog.google"),
        (r"\b(?:samsung|самсунг)\b", "samsung.com"),
        (r"\bgithub\b", "github.com"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            domains.append(domain)
    return tuple(domains)


def build_search_query(text: str) -> str:
    text = normalize_search_entities(text)
    iphone = re.search(r"\biphone\s+(\d+)(?:\s+(pro|про)(?:\s+(max|макс))?)?\b", text, re.IGNORECASE)
    if iphone and _RELEASE_QUESTION.search(text):
        product = (
            f"iPhone {iphone.group(1)}" + (" Pro" if iphone.group(2) else "") + (" Max" if iphone.group(3) else "")
        )
        return f"{product} release date"
    if (
        _FRESHNESS.search(text)
        and re.search(r"\bgemini\b", text, re.IGNORECASE)
        and re.search(r"лимит\w*|rate limits", text, re.IGNORECASE)
    ):
        return "current Gemini API rate limits"
    if _FRESHNESS.search(text) and re.search(r"\b(?:верси\w*|модел\w*|version|model)\b", text, re.IGNORECASE):
        # Keep specific releases and pricing/news questions intact.
        if not re.search(r"\d|цен\w*|курс\w*|лимит\w*|pricing|price|news|новост\w*", text, re.IGNORECASE):
            domains = official_domains_for_query(text)
            if domains == ("openai.com",):
                return (
                    "latest OpenAI GPT model" if re.search(r"\bgpt\b", text, re.IGNORECASE) else "latest OpenAI model"
                )
            if domains == ("python.org",):
                return "latest stable Python version"
    return text


def is_identity_question(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:ты какая модель|какая модель ты|какая у тебя модель|на какой модели ты работаешь|"
            r"кто ты|what model are you|who are you)[?!.\s]*",
            text,
            re.IGNORECASE,
        )
    )


def extract_current_question(text: str) -> str:
    """Route the new question rather than the quoted reply context assembled by gpt_router."""
    if "Контекст из предыдущего сообщения:\n---\n" in text:
        _, separator, question = text.rpartition("\n---\n\n")
        if separator:
            return question
    return text


def should_force_web_search(text: str) -> bool:
    """Conservative safety net for explicit, topical freshness questions, not a semantic classifier."""
    text = normalize_search_entities(extract_current_question(text))
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    if _TRANSFORM.search(text):
        return False
    has_freshness = bool(_FRESHNESS.search(text))
    if _CODE.search(text) and not (
        has_freshness and re.search(r"верси\w*|version\w*|api|документаци\w*", text, re.IGNORECASE)
    ):
        return False
    if has_freshness and _DYNAMIC_TOPIC.search(text):
        return True
    if (
        has_freshness
        and re.search(r"последн\w*|ласт\w*", text, re.IGNORECASE)
        and re.search(r"\bopenai\b", text, re.IGNORECASE)
    ):
        return True
    if (
        has_freshness
        and re.search(r"\b(?:btc|bitcoin|биткоин\w*)\b", text, re.IGNORECASE)
        and re.search(r"какой|сколько|цена|курс", text, re.IGNORECASE)
    ):
        return True
    if _LATEST.search(text) and _TECH.search(text):
        return True
    return bool(_RELEASE_QUESTION.search(text) and _PRODUCT.search(text))


def requests_sources(text: str) -> bool:
    return bool(
        re.search(
            r"(?:дай|покажи|скинь|приведи|укажи|добавь).{0,25}(?:источник\w*|ссылк\w*|url)|"
            r"(?:give|show|provide|include).{0,25}(?:sources?|links?|urls?)",
            text,
            re.IGNORECASE,
        )
    )
