from http import HTTPStatus

from httpx import AsyncClient, HTTPStatusError

from core.config import BACKEND_ROUTE


async def get_horoscope_mail_ru(zodiac_eng: str) -> dict[str, str]:
    """
    Получает гороскоп с сайта mail.ru по заданному знаку зодиака.
    """
    url = f"{BACKEND_ROUTE}/horoscope/{zodiac_eng}"
    async with AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except HTTPStatusError as e:
            if e.response.status_code == HTTPStatus.NOT_FOUND:
                raise ValueError("Знак зодиака не найден.") from e
            else:
                raise RuntimeError(f"Ошибка при получении гороскопа: {e}") from e


def format_horoscope(horoscope: dict[str, str]) -> str:
    """
    Форматирует гороскоп в удобочитаемый вид.
    """
    return (
        f"{horoscope['sign']}{horoscope['emoji']}:\n"
        f"{horoscope['daily']}\n"
        f"Финансовый гороскоп: {horoscope['finance']}"
    )
