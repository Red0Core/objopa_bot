from typing import cast

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.config import GIFS_ID, WOLFRAMALPHA_TOKEN
from core.logger import logger
from tg_bot.services.horoscope_mail_ru import format_horoscope, get_horoscope_mail_ru

router = Router()


@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer_animation(
        animation=GIFS_ID["Салам дай брад"],
        caption="MEXC за идею.\nА тут /cbr - курсы валют на сегодня и каждый день в 12 отправляю\n/price BTC и тд с бинанса выкачивает прайс\nПока что все",
    )


@router.message(Command("horoscope"))
async def horoscope_command(message: Message):
    zodiac_map = {
        "taurus": "телец",
        "cancer": "рак",
        "libra": "весы",
        "scorpio": "скорпион",
        "sagittarius": "стрелец",
        "capricorn": "козерог",
        "aquarius": "водолей",
        "pisces": "рыбы",
        "aries": "овен",
        "gemini": "близнецы",
        "leo": "лев",
        "virgo": "дева",
    }
    try:
        # Получает знак зодиака из сообщения
        zodiac_sign = cast(str, message.text).split()[1].lower()
        # Проверяет если знак зодиака на русском или на английском
        reversed_zodiac_map = {v: k for k, v in zodiac_map.items()}
        if zodiac_sign in reversed_zodiac_map:
            zodiac_eng = reversed_zodiac_map[zodiac_sign]
            text = format_horoscope(await get_horoscope_mail_ru(zodiac_eng))
        else:
            text = format_horoscope(await get_horoscope_mail_ru(zodiac_sign))
        await message.answer(text=text)
        logger.info(f"Отправляем гороскоп в чат {message.chat.id} для {zodiac_sign}")
        return
    except (IndexError, KeyError):
        await message.answer(
            "Пожалуйста, укажите знак зодиака на английском или русском. Например: /horoscope libra или /horoscope весы"
        )
        return


@router.message(Command("calc"))
async def calculator_wolframaplha_math(message: Message):
    arr = cast(str, message.text).split(maxsplit=1)
    if len(arr) == 2:
        try:
            from sympy import N, sympify

            result = float(N(sympify(arr[1], evaluate=True)))
            await message.answer(str(result))
        except Exception:
            import wolframalpha  # type: ignore

            client = wolframalpha.Client(WOLFRAMALPHA_TOKEN)
            res = await client.aquery(arr[1])  # type: ignore
            await message.answer(next(res.results).text)
    else:
        await message.answer("Использовать /calc и тут ваша матеша")


# Генерация меню игр
def games_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🃏 Блэкджек", callback_data="start_blackjack")],
            [InlineKeyboardButton(text="Закрыть", callback_data="close_menu")],
        ]
    )
    return keyboard


@router.message(Command("games"))
async def games_command(message: Message):
    await message.answer("Выберите игру из списка:", reply_markup=games_menu())


@router.callback_query(lambda c: c.data == "close_menu")  # type: ignore
async def close_menu(callback: CallbackQuery):
    if callback.message is not None:
        await callback.message.edit_text("Меню игр закрыто.", reply_markup=None)  # type: ignore
    await callback.answer()
