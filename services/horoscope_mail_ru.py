from curl_cffi.requests import AsyncSession
from lxml import html

async def fetch_html(url):
    async with AsyncSession() as session:
        return (await session.get(url)).text

async def get_daily_horoscope_with_rating(zodiac_english):
    """
    Извлекает рейтинг (например, "4 из 5") из страницы предсказания по заданному знаку.
    Рейтинг расположен в блоке, где есть дочерний <div> с <a> содержащим "Финансы"
    и внутри которого находится <ul> с aria-label.
    """
    url = f"https://horo.mail.ru/prediction/{zodiac_english}/today/"
    page = await fetch_html(url)
    doc = html.fromstring(page)
    elements = doc.xpath('//*[@data-qa="ArticleLayout"]')
    # Собираем текст из всех найденных элементов
    daily_horoscope = " ".join([el.text_content().strip() for el in elements])

    rating = []
    # Находим родительский <div>, в котором есть дочерний <div> с <a>, содержащим "Финансы"
    div_parent = doc.xpath('//div[./div[a[contains(text(),"Финансы")]]]')
    if div_parent:
        # Ищем дочерние div внутри найденного родительского блока
        child_divs = div_parent[0].xpath('.//div')
        for child in child_divs:
            a_tag = child.xpath('.//a')
            ul_tag = child.xpath('.//ul[@aria-label]')
            if a_tag and ul_tag:
                label = a_tag[0].text_content().strip()
                if label.lower() == "финансы":
                    label = "\U0001F4B0"
                elif label.lower() == "здоровье":
                    label  = "\U0001F3E5"
                elif label.lower() == "любовь":
                    label = "\U0001F495"
                rating.append(f'{label} {ul_tag[0].attrib.get("aria-label").strip()}')
    return f'{daily_horoscope}\n{" ".join(rating)}' or 'нет данных'

async def get_financial_horoscope(zodiac_russian):
    """
    Извлекает текст финансового гороскопа из таблицы на странице финансового гороскопа.
    Находит строку таблицы, где первый <td> соответствует знаку, а второй содержит описание.
    """
    finance_url = "https://horo.mail.ru/article/finance-daily-horoscope/"
    page = await fetch_html(finance_url)
    doc = html.fromstring(page)
    table_elements = doc.xpath('//*[@data-logger="ArticleContent_table"]')
    if table_elements:
        rows = table_elements[0].xpath('.//tr')
        for row in rows:
            tds = row.xpath('.//td')
            if len(tds) >= 2:
                sign = tds[0].text_content().strip().lower()
                if sign == zodiac_russian:
                    return tds[1].text_content().strip()
    return "нет данных"

async def get_horoscope_mail_ru(zodiac_eng, zodiac_ru):
    zodiac_emoji = {
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
    daily = await get_daily_horoscope_with_rating(zodiac_eng)
    finance_text = (await get_financial_horoscope(zodiac_ru)).strip()
    # Формируем единый вывод для каждого знака
    return f"{zodiac_ru.capitalize()}{zodiac_emoji[zodiac_eng]}:\n{daily}\nФинансовый гороскоп: {finance_text}\n"
