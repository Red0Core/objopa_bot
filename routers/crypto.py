import pprint
from services.coinmarketcap import *
from services.exchanges import get_price_from_exchanges
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
import traceback

router = Router()

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
    args = message.text.split(maxsplit=3)
    if len(args) < 2:
        await message.reply("Укажите тикер койна. Пример /cmc BTC")
        return

    symbol = args[1].upper()
    try:
        # Заготовленные функции для вывода CoinMarketCap
        num_of_tokens = float(args[2].replace(',', '.')) if len(args) >= 3 else float(0)
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

@router.message(Command("cmcwl"))
async def add_to_whitelist_coinmarketcap_handler(message: Message):
    """
    Добавялет нужный тикер, если фильтруется он coinmarketcap
    """
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("Укажите тикер койна и его имя. Пример /cmcwl MKL Merkle Trade")
        return
    
    symbol = args[1].upper()
    name = args[2]

    for i in (await get_coinmarketcap_data(symbol))['data'][symbol]:
        if name == i['name']:
            add_to_whitelist("whitelist.json", symbol, name)
            await message.reply(f"Успешно добавлен {name} под тикером {symbol}")
            return

    await message.reply(f"Вы не правильно указали имя или тикер")
    return
