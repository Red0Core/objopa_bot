from curl_cffi.requests import AsyncSession
from lxml import html, etree
from typing import cast

ZODIAC_RU_MAP = {
    "taurus": "Телец",
    "cancer": "Рак",
    "libra": "Весы",
    "scorpio": "Скорпион",
    "sagittarius": "Стрелец",
    "capricorn": "Козерог",
    "aquarius": "Водолей",
    "pisces": "Рыбы",
    "aries": "Овен",
    "gemini": "Близнецы",
    "leo": "Лев",
    "virgo": "Дева",
}

ZODIAC_EMOJI = {
    "taurus" : "♉",
    "cancer" : "♋",
    "libra" : "♎",
    "scorpio" : "♏",
    "sagittarius" : "♐",
    "capricorn" : "♑",
    "aquarius" : "♒",
    "pisces" : "♓",
    "aries" : "♈",
    "gemini" : "♊",
    "leo" : "♌",
    "virgo" : "♍"
}

async def fetch_html(url: str) -> str:
    async with AsyncSession() as session:
        return (await session.get(url)).text

async def get_daily_horoscope_with_rating(zodiac_english: str) -> str:
    """
    Извлекает рейтинг (например, "4 из 5") из страницы предсказания по заданному знаку.
    Рейтинг расположен в блоке, где есть дочерний <div> с <a> содержащим "Финансы"
    и внутри которого находится <ul> с aria-label.
    """
    url = f"https://horo.mail.ru/prediction/{zodiac_english}/today/"
    page = await fetch_html(url)
    doc = cast(etree._Element, html.fromstring(page))  # type: ignore[attr-defined]
    elements = doc.xpath('//*[@data-qa="ArticleLayout"]')
    # Собираем текст из всех найденных элементов
    daily_horoscope = " ".join([el.text_content().strip() for el in elements]) # type: ignore[attr-defined]

    rating: list[str] = []
    # Находим родительский <div>, в котором есть дочерний <div> с <a>, содержащим "Финансы"
    div_parent = doc.xpath('//div[./div[a[contains(text(),"Финансы")]]]')
    if div_parent:
        # Ищем дочерние div внутри найденного родительского блока
        child_divs = div_parent[0].xpath('.//div') # type: ignore
        for child in child_divs: # type: ignore
            a_tag = child.xpath('.//a') # type: ignore
            ul_tag = child.xpath('.//ul[@aria-label]') # type: ignore
            if a_tag and ul_tag:
                label = cast(str, a_tag[0].text_content()) # type: ignore
                label = label.strip().lower()
                if label == "финансы":
                    label = "\U0001F4B0"
                elif label == "здоровье":
                    label  = "\U0001F3E5"
                elif label == "любовь":
                    label = "\U0001F495"
                rating_str = ul_tag[0].attrib.get("aria-label").strip() # type: ignore
                rating.append(f'{label} {rating_str}')
    return f'{daily_horoscope}\n{" ".join(rating)}' or 'нет данных'

async def get_financial_horoscope(zodiac_russian: str) -> str:
    """
    Извлекает текст финансового гороскопа из таблицы на странице финансового гороскопа.
    Находит строку таблицы, где первый <td> соответствует знаку, а второй содержит описание.
    """
    finance_url = "https://horo.mail.ru/article/finance-daily-horoscope/"
    page = await fetch_html(finance_url)
    doc = cast(etree._Element, html.fromstring(page))  # type: ignore[attr-defined]
    table_elements = doc.xpath('//*[@data-logger="ArticleContent_table"]')
    if table_elements:
        rows = table_elements[0].xpath('.//tr') # type: ignore
        for row in rows: # type: ignore
            tds = row.xpath('.//td') # type: ignore
            if len(tds) >= 2: # type: ignore
                sign = cast(str, tds[0].text_content()).strip().lower() # type: ignore
                if sign == zodiac_russian:
                    return cast(str, tds[1].text_content()).strip() # type: ignore
    return "нет данных"

async def get_horoscope_mail_ru(zodiac_eng: str) -> dict[str, str]:
    zodiac_eng = zodiac_eng.lower()
    zodiac_ru = ZODIAC_RU_MAP.get(zodiac_eng, zodiac_eng.capitalize())
    emoji = ZODIAC_EMOJI.get(zodiac_eng, "")

    daily = await get_daily_horoscope_with_rating(zodiac_eng)
    finance = await get_financial_horoscope(zodiac_ru.lower())

    return {
        "sign": zodiac_ru,
        "emoji": emoji,
        "daily": daily,
        "finance": finance,
    }