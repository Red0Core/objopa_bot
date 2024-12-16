from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import GIFS_ID
from services.cbr import get_cbr_exchange_rate
from services.coinmarketcap import *
from services.exchanges import get_price_from_exchanges
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
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Укажите тикер койна. Пример /cmc BTC")
        return

    symbol = args[1].upper()
    try:
        output = format_crypto_price(
                    filter_tickers(
                        (await get_coinmarketcap_data(symbol))['data'][symbol]
                    )
                )
    except Exception as e:
        logger.error(f"Ошибка coinmarketcap: {traceback.format_exc()}")
        output = "Ошибка, напишите позднее"

    await message.reply(output)

@router.message(Command("cbr"))
async def get_cbr_rates_handler(message: Message):
    rates = await get_cbr_exchange_rate()
    if "error" in rates:
        logger.error(f"Ошибка при получении курсов: {rates['error']}")
        await message.reply(f"Ошибка при получении курсов: {rates['error']}")
    else:
        usd_rate = rates["USD"]["rate"]
        eur_rate = rates["EUR"]["rate"]
        usd_diff = rates["USD"]["diff"]
        eur_diff = rates["EUR"]["diff"]

        logger.info(f"Отправляем курсы валют пользователю {message.from_user.id}")

        await message.reply(
            f"Курсы валют ЦБ РФ на сегодня:\n"
            f"💵 Доллар США: {usd_rate} ₽ ({'+' if usd_diff > 0 else ''}{usd_diff})\n"
            f"💶 Евро: {eur_rate} ₽ ({'+' if eur_diff > 0 else ''}{eur_diff})"
        )
