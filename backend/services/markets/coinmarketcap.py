from pathlib import Path
from typing import Any
from httpx import AsyncClient
from config import COINMARKETCAP_API_KEY
from logger import logger
import ujson

from typing import TypedDict

class QuoteData(TypedDict):
    price: float
    percent_change_1h: float
    percent_change_24h: float
    percent_change_7d: float
    percent_change_30d: float
    market_cap: float | None
    volume_24h: float

class CoinData(TypedDict):
    name: str
    symbol: str
    is_active: int
    quote: dict[str, QuoteData]

COINMARKETCAP_WHITELIST = Path('storage') / "whitelist_coinmarketcap.json"
COINMARKETCAP_WHITELIST.parent.mkdir(parents=True, exist_ok=True)

def add_to_whitelist(symbol: str, name: str, file_path: Path = COINMARKETCAP_WHITELIST) -> bool:
    """
    Добавляет в белый список имя и его тикер
    
    :param file_path: Путь к JSON-файлу с белым списком.
    """
    data: dict[str, list[str]]
    if not file_path.exists():
        data = {}
        # Словарь вида: {"BTC": ["bitcoin", "биток"]}
    else:
        # Загружаем существующий белый список
        try:
            with open(file_path, "r") as file:
                data = ujson.load(file)
        except (ujson.JSONDecodeError, FileNotFoundError):
            logger.error(f"Ошибка при чтении файла {file_path}. Создаю новый файл.")
            data = {}

    if symbol in data:
        if name not in data[symbol]:
            data[symbol].append(name)
            logger.info(f"Добавлено имя '{name}' для тикера '{symbol}'.")
        else:
            logger.info(f"Имя '{name}' уже существует для тикера '{symbol}'.")
    else:
        data[symbol] = [name]
        logger.info(f"Добавлен новый тикер '{symbol}' с именем '{name}'.")

    # Сохраняем изменения в файл
    with open(file_path, "w") as file:
        ujson.dump(data, file, indent=4)
        logger.info(f"Белый список успешно обновлён.")
    
    return True


def load_whitelist(symbol: str, file_path: Path = COINMARKETCAP_WHITELIST) -> list[str]:
    """
    Загружает белый список токенов из файла.
    
    :param file_path: Путь к JSON-файлу с белым списком.
    :return: Список символов токенов из белого списка.
    """
    try:
        with open(file_path, "r") as file:
            data = ujson.load(file)
            return data.get(symbol, [])
    except FileNotFoundError:
        logger.error(f"Файл {file_path} не найден.")
        return []
    except ujson.JSONDecodeError:
        logger.error(f"Файл {file_path} содержит некорректный JSON.")
        return []

def format_crypto_price(data: list[CoinData], amount_of_tokens: float = 0.0):
    """
    Форматирует данные о криптовалюте для красивого вывода.
    """
    output = ""
    for coin_data in data:
        quote = coin_data.get("quote", {}).get("USD", {})
        price = float(quote.get('price', 0))

        if not coin_data or not quote:
            return "Ошибка: данные о криптовалюте отсутствуют."

        # Формируем красиво оформленное сообщение
        message = (
            f"🔹 {coin_data.get('name', 'N/A')} (<code>{coin_data.get('symbol', 'N/A')}</code>)\n"
            f"💵 <b>Цена:</b> ${price:.5f}\n"
            f"📊 <b>Изменения:</b>\n"
            f"  - За 1 час: {quote.get('percent_change_1h', 0):+.2f}%\n"
            f"  - За 24 часа: {quote.get('percent_change_24h', 0):+.2f}%\n"
            f"  - За 7 дней: {quote.get('percent_change_7d', 0):+.2f}%\n"
            f"  - За 30 дней: {quote.get('percent_change_30d', 0):+.2f}%\n"
            f"💹 <b>Капа:</b> ${quote.get('market_cap', 0):,.2f}\n"
            f"🔄 <b>Объем за 24 часа:</b> ${quote.get('volume_24h', 0):,.2f}\n"
        )

        # Выводит сумму баксов по прайсу токена
        if amount_of_tokens > 0:
            message = f"{message}{amount_of_tokens:.5f} * {price:.5f} = <code>{(amount_of_tokens*price):,.5f}</code>💲"

        output = f"{output}\n{message}"

    return output or "Ошибка: данные о криптовалюте отсутствуют."

def filter_tickers(data: list[CoinData], file_path: Path = COINMARKETCAP_WHITELIST) -> list[CoinData]:
    """
    Фильтрует список тикеров на основе заданных условий.
    
    :param data: Данные из API CoinMarketCap.
    :return: Список тикеров, прошедших фильтр.
    """
    filtered_tickers: list[CoinData] = []
    for coin_data in data:
        whitelist = load_whitelist(coin_data['symbol'], file_path)
        market_cap = coin_data["quote"]["USD"]["market_cap"]
        try:
            if coin_data['name'] not in whitelist: # Не скипаем белый лист
                if market_cap is None or market_cap < 1:
                    continue  # Пропустить, если капитализация меньше заданной
                if coin_data["is_active"] != 1:
                    continue  # Пропустить, если токен неактивен
            
            # Если тикер прошел все условия, добавляем его в результат
            filtered_tickers.append(coin_data)
        except TypeError:
            logger.info(f"Данные тикера: {ujson.dumps(coin_data, indent=5)}")

    return filtered_tickers

async def get_coinmarketcap_data(ticker: str, **params: dict[str, Any]) -> CoinData:
    ticker = ticker.upper()
    headers = {
        'Accepts': 'application/json',
        'X-CMC_PRO_API_KEY': COINMARKETCAP_API_KEY,
    }
    parameters: dict[str, Any] = {
        'symbol': ticker,
        **params
    }
    async with AsyncClient() as session:
        session.headers.update(headers)
        response = await session.get("https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest", params=parameters)
        response.raise_for_status()  # Проверяем статус ответа
        
        json_data = response.json()
        if json_data.get("data") is None:
            logger.error(f"Данные от CMC не поступили: {ujson.dumps(json_data, indent=5)}")
        return json_data
