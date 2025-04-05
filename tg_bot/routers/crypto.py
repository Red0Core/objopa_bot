from services.coinmarketcap import *
from services.exchanges import get_price_from_exchanges
import services.bybit_p2p as p2p
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
import traceback
from logger import logger

router = Router()

@router.message(Command("price"))
async def get_price_handler(message: Message):
    """
    Выводит цену из Бинанса или Мекса
    """
    # Парсим аргумент команды
    args = message.text.split(maxsplit=1) if message.text else []
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
    args = message.text.split(maxsplit=3) if message.text else []
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
    args = message.text.split(maxsplit=2) if message.text else []
    if len(args) < 3:
        await message.reply("Укажите тикер койна и его имя. Пример /cmcwl MKL Merkle Trade")
        return
    
    symbol = args[1].upper()
    name = args[2]

    for i in (await get_coinmarketcap_data(symbol))['data'][symbol]:
        if name == i['name']:
            add_to_whitelist(symbol, name)
            await message.reply(f"Успешно добавлен {name} под тикером {symbol}")
            return

    await message.reply(f"Вы не правильно указали имя или тикер")
    return

@router.message(Command("p2p"))
async def current_p2p_bybit_orders(message: Message):
    """
    Выводит текущие сообщения из p2p
    """
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.reply("Примерк команд. Пример: /p2p buy | /p2p sell | /p2p buy 1000 USDT | /p2p buy 1000 RUB")
        return

    symbol = args[1].upper()
    if symbol not in ["BUY", "SELL"]:
        await message.reply("Укажите покупку или продажу тейкером. Пример: /p2p buy | /p2p sell | /p2p buy 1000 USDT | /p2p buy 1000 RUB")
        return
    
    if len(args) > 2 and len(args) != 4:
        await message.reply("Укажите сумму и валюту. Пример: /p2p buy 1000 USDT | /p2p buy 1000 RUB")
        return
    
    amount = None
    try:
        if len(args) == 4:
            amount = float(args[2])
            is_fiat = args[3].upper() == "RUB"
    except (ValueError, IndexError):
        await message.reply("Укажите сумму и валюту. Пример: /p2p buy 1000 USDT | /p2p buy 1000 RUB")
        return

    # Получаем данные из p2p
    try:
        data = await p2p.get_p2p_orders(symbol == "BUY")
    except Exception as e:
        logger.error(f"Ошибка p2p: {traceback.format_exc()}")
        await message.reply("Ошибка, напишите позднее")
        return

    # Это получение просто всех ордеров или с суммой?
    if amount:
        offers = p2p.get_offers_by_amount(data, amount, is_fiat)
        await message.reply(p2p.generate_amount_html_output(p2p.get_offers_by_valid_makers(offers), amount, is_fiat))

    else:
        categorized_data = p2p.categorize_all_offers(data)
        for label in categorized_data:
            categorized_data[label] = p2p.get_offers_by_valid_makers(categorized_data[label])
        
        await message.reply(p2p.generate_categories_html_output(categorized_data), parse_mode="html")
