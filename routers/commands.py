from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import GIFS_ID
from services.binance import get_binance_price
from services.cbr import get_cbr_exchange_rate
from logger import logger

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
    price = await get_binance_price(symbol)
    if price:
        await message.reply(f"Цена {symbol}: {price:.16g} USD")
    else:
        await message.reply(f"Не удалось получить цену для {symbol}. Проверьте символ.")

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
