import asyncio
import traceback
from typing import Any

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.config import BACKEND_ROUTE
from core.logger import logger

router = Router()


@router.message(Command("cbr"))
async def get_cbr_rates_handler(message: Message):
    """
    Отправляет курсы ЦБ РФ за сегодня и его изменение
    """
    logger.info(
        f"Отправляем курсы валют пользователю {message.from_user.id if message.from_user else 'неизвестный'}"
    )
    try:
        async with httpx.AsyncClient() as session:
            response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates")
            response.raise_for_status()
            data = response.json()
            await message.reply(data["html_output"], parse_mode="html")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await message.reply("Ошибка: неверный запрос")
        else:
            await message.reply("Ошибка: не удалось получить данные")


@router.message(Command("rub"))
async def get_forex_rub_rates_handler(message: Message):
    """
    Отправляет текстовое сообщение и график курса валют в Telegram.
    """
    output = ""
    try:
        async with httpx.AsyncClient() as session:
            response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates")
            response.raise_for_status()
            data = response.json()
            output = data["html_output"]

            async def fetch_currency_data(from_symbol: str, to_symbol: str) -> dict[str, Any]:
                response = await session.get(
                    f"{BACKEND_ROUTE}/markets/forex/{from_symbol}/{to_symbol}"
                )
                response.raise_for_status()
                return response.json()

            arr = asyncio.gather(
                fetch_currency_data("USD", "RUB"), fetch_currency_data("EUR", "RUB")
            )
            for data in await arr:
                symbol = data["base"]
                market = data["quote"]
                today = data["rate"]
                change_1d = (
                    data["changes"]["day1"]["absolute"],
                    data["changes"]["day1"]["percent"],
                )
                change_7d = (
                    (data["changes"]["day7"]["absolute"], data["changes"]["day7"]["percent"])
                    if data["changes"]["day7"]
                    else None
                )
                change_30d = (
                    (data["changes"]["day30"]["absolute"], data["changes"]["day30"]["percent"])
                    if data["changes"]["day30"]
                    else None
                )
                # Формируем сообщение
                output += (
                    f"\n💹 <b>Курс {symbol}/{market}:</b>\n"
                    f"Текущая цена: <code>{today:.2f} {market}</code>\n"
                    f"🔸 За 1 день: <code>{change_1d[0]:+.2f} ({change_1d[1]:+.2f}%)</code>\n"
                )
                if change_7d:
                    output += (
                        f"🔹 За 7 дней: <code>{change_7d[0]:+.2f} ({change_7d[1]:+.2f}%)</code>\n"
                    )
                if change_30d:
                    output += f"🔸 За 30 дней: <code>{change_30d[0]:+.2f} ({change_30d[1]:+.2f}%)</code>\n"

    except httpx.HTTPStatusError:
        output = f"{output}\nОшибка: не удалось получить данные"
        await message.reply(output, parse_mode="html")
        return
    except Exception as e:
        logger.error(f"Ошибка: {traceback.format_exc()}")
        await message.reply(f"Ошибка: {e}")
        return

    await message.reply(output, parse_mode="html")
    logger.info(
        f"Успешно отправил рубль для {message.from_user.id if message.from_user else 'unknown'}"
    )
