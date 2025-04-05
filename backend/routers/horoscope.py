from fastapi import APIRouter
from backend.services.misc.horoscope_mail_ru import get_daily_horoscope_with_rating, get_financial_horoscope, ZODIAC_RU_MAP, ZODIAC_EMOJI
from backend.models.horoscope import HoroscopeResponse

router = APIRouter(prefix="/horoscope", tags=["horoscope"])

@router.get("/{zodiac_eng}")
async def get_horoscope_mail_ru(zodiac_eng: str) -> HoroscopeResponse:
    zodiac_eng = zodiac_eng.lower()
    zodiac_ru = ZODIAC_RU_MAP.get(zodiac_eng, zodiac_eng.capitalize())
    emoji = ZODIAC_EMOJI.get(zodiac_eng, "")

    daily = await get_daily_horoscope_with_rating(zodiac_eng)
    finance = await get_financial_horoscope(zodiac_ru.lower())

    return HoroscopeResponse(
        sign=zodiac_ru,
        emoji=emoji,
        daily=daily,
        finance=finance,
    )