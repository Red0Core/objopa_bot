from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from services.cbr import generate_cbr_output
from services.alphavantage import fetch_currency_data, parse_currency_data, calculate_change
from logger import logger
import asyncio
import traceback

router = Router()

@router.message(Command("cbr"))
async def get_cbr_rates_handler(message: Message):
    """
    Отправляет курсы ЦБ РФ за сегодня и его изменение
    """
    logger.info(f"Отправляем курсы валют пользователю {message.from_user.id}")
    await message.reply(await generate_cbr_output())

@router.message(Command("rub"))
async def get_forex_rub_rates_handler(message: Message):
    """
    Отправляет текстовое сообщение и график курса валют в Telegram.
    """
    output = await generate_cbr_output()
    try:
        # Получаем данные
        arr = asyncio.gather(fetch_currency_data("USD", "RUB"), fetch_currency_data("EUR", "RUB"))
        for data in (await arr):
            symbol = data["Meta Data"]["2. From Symbol"]
            market = data["Meta Data"]["3. To Symbol"]

            today, yesterday, price_7d, price_30d = parse_currency_data(data)

            change_1d = calculate_change(today, yesterday)
            change_7d = calculate_change(today, price_7d)
            change_30d = calculate_change(today, price_30d)

            # Формируем сообщение
            output += (
                    f"\n💹 <b>Курс {symbol}/{market}:</b>\n"
                    f"Текущая цена: <code>{today:.2f} {market}</code>\n"
                    f"🔸 За 1 день: <code>{change_1d[0]:+.2f} ({change_1d[1]:+.2f}%)</code>\n"
                )
            if price_7d:
                output += f"🔹 За 7 дней: <code>{change_7d[0]:+.2f} ({change_7d[1]:+.2f}%)</code>\n"
            if price_30d:
                output += f"🔸 За 30 дней: <code>{change_30d[0]:+.2f} ({change_30d[1]:+.2f}%)</code>\n"

    except Exception as e:
        logger.error(f"Ошибка: {traceback.format_exc()}")
        await message.reply(f"Ошибка: {e}")

    await message.reply(output, parse_mode="html")
    logger.info(f"Успешно отправил рубль для {message.from_user.id}")