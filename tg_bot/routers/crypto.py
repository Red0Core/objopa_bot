import traceback
from typing import cast

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from httpx import AsyncClient, HTTPStatusError

from core.config import BACKEND_ROUTE
from core.logger import logger

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
    async with AsyncClient() as session:
        try:
            result = await session.get(f"{BACKEND_ROUTE}/markets/price/{symbol}")
            result.raise_for_status()
            result_dict = cast(dict[str, str | float], result.json())
            await message.reply(f"Цена {result_dict['symbol']}: {result_dict['price']} USDT")
        except HTTPStatusError as e:
            if e.response.status_code == 400:
                await message.reply("Ошибка: неверный символ")
            else:
                logger.error(f"Ошибка price: {traceback.format_exc()}")
                await message.reply("Ошибка, напишите позднее")
            return


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
    output = ""
    try:
        # Заготовленные функции для вывода CoinMarketCap
        num_of_tokens = float(args[2].replace(",", ".")) if len(args) >= 3 else None
        async with AsyncClient() as session:
            response = await session.get(
                f"{BACKEND_ROUTE}/markets/crypto/{symbol}",
                params={"amount": num_of_tokens} if num_of_tokens else {},
            )
            response.raise_for_status()
            output = response.json()["html_output"]

    except HTTPStatusError as e:
        if e.response.status_code == 400:
            await message.reply("Ошибка: неверный тикер")
        else:
            logger.error(f"Ошибка coinmarketcap: {traceback.format_exc()}")
            await message.reply("Ошибка, напишите позднее")
        return

    await message.reply(output)
    logger.info(
        f"Успешно отправлен coinmarketcap {args[1]} к {message.from_user.id if message.from_user else 'unknown'}"
    )


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

    try:
        async with AsyncClient() as session:
            response = await session.post(
                f"{BACKEND_ROUTE}/markets/crypto/whitelist", json={"symbol": symbol, "name": name}
            )
            response.raise_for_status()
            if response.status_code == 201:
                await message.reply(f"Добавлен {name} ({symbol}) в whitelist")
                return
    except HTTPStatusError as e:
        if e.response.status_code == 400:
            await message.reply("Ошибка: неверный тикер или имя")
        else:
            logger.error(f"Ошибка coinmarketcap whitelist: {traceback.format_exc()}")
            await message.reply("Ошибка, напишите позднее")

    await message.reply("Вы не правильно указали имя или тикер")
    return


@router.message(Command("p2p"))
async def current_p2p_bybit_orders(message: Message):
    """
    Выводит текущие сообщения из p2p
    """
    args = message.text.split() if message.text else []
    if len(args) != 4:
        await message.reply("Примерк команд. Пример: /p2p buy 1000 USDT | /p2p buy 1000 RUB")
        return

    symbol = args[1].upper()
    if symbol not in ["BUY", "SELL"]:
        await message.reply(
            "Укажите покупку или продажу тейкером. Пример: /p2p buy | /p2p sell | /p2p buy 1000 USDT | /p2p buy 1000 RUB"
        )
        return

    amount = None
    is_fiat = False
    try:
        if len(args) == 4:
            amount = float(args[2])
            is_fiat = args[3].upper() == "RUB"
    except (ValueError, IndexError):
        await message.reply(
            "Укажите сумму и валюту. Пример: /p2p buy 1000 USDT | /p2p buy 1000 RUB"
        )
        return

    # Получаем данные из p2p
    async with AsyncClient() as session:
        try:
            response = await session.get(
                f"{BACKEND_ROUTE}/markets/p2p",
                params={"is_buy": ("BUY" == symbol), "amount": amount, "is_fiat": is_fiat},
            )
            response.raise_for_status()
            result = response.json()
            await message.reply(result["html_output"])
        except HTTPStatusError as e:
            if e.response.status_code == 500:
                await message.reply("Ошибка сервера, напишите позднее")
            else:
                logger.error(f"Ошибка p2p: {traceback.format_exc()}")
                await message.reply("Ошибка, напишите позднее")
            await message.reply("Ошибка, напишите позднее")
            return
