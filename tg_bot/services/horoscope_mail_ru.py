from httpx import AsyncClient

async def get_horoscope_mail_ru(zodiac_eng: str) -> dict[str, str]:
    """
    Получает гороскоп с сайта mail.ru по заданному знаку зодиака.
    """
    url = f"http://localhost:8000/horoscope/{zodiac_eng}"
    async with AsyncClient() as client:
        response = await client.get(url)
        return response.json()

def format_horoscope(horoscope: dict[str, str]) -> str:
    """
    Форматирует гороскоп в удобочитаемый вид.
    """
    return (
        f"{horoscope['sign']}{horoscope['emoji']}:\n"
        f"{horoscope['daily']}\n"
        f"Финансовый гороскоп: {horoscope['finance']}"
    )