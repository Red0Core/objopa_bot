from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import GIFS_ID
from routers.mention_dice import gpt_to_telegram_markdown_v2
from services.cbr import generate_cbr_output
from services.coinmarketcap import *
from services.exchanges import get_price_from_exchanges
from services.alphavantage import fetch_currency_data, parse_currency_data, calculate_change
from logger import logger
import asyncio

import traceback

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer_animation(
        animation=GIFS_ID["Салам дай брад"],
        caption="MEXC за идею.\nА тут /cbr - курсы валют на сегодня и каждый день в 12 отправляю\n/price BTC и тд с бинанса выкачивает прайс\nПока что все"
    )

@router.message(Command("price"))
async def get_price_handler(message: Message):
    """
    Выводит цену из Бинанса или Мекса
    """
    # Парсим аргумент команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Укажите символ валютной пары. Пример: /price BTCUSDT")
        return

    symbol = args[1].upper()
    result = await get_price_from_exchanges(symbol)

    if "error" in result:
        await message.reply(f"Ошибка: {result['error']}")
    else:
        await message.reply(f"Цена {result['symbol']}: {result['price']} USDT")

@router.message(Command("cmc"))
async def get_cmc_handler(message: Message):
    """
    Выводит данные токена из coinmarketcap
    """
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.reply("Укажите тикер койна. Пример /cmc BTC")
        return

    symbol = args[1].upper()
    try:
        # Заготовленные функции для вывода CoinMarketCap
        num_of_tokens = float(args[2].replace(',', '.'))
        output = format_crypto_price(
                    filter_tickers(
                        (await get_coinmarketcap_data(symbol))['data'][symbol],
                    ),
                    num_of_tokens
                )

    except Exception as e:
        logger.error(f"Ошибка coinmarketcap: {traceback.format_exc()}")
        output = "Ошибка, напишите позднее"
        await message.reply(output)
        return

    await message.reply(output)
    logger.info(f"Успешно отправлен coinmarketcap {args[1]} к {message.from_user.id}")

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
