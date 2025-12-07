import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import httpx

from backend.models.markets import CBRValuteItem
from core.logger import logger
from core.redis_client import get_redis

SOAP_URL = "http://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
SOAP_ENVELOPE_KEYRATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <KeyRate xmlns="http://web.cbr.ru/">
      <fromDate>{from_dt}</fromDate>
      <ToDate>{to_dt}</ToDate>
    </KeyRate>
  </soap:Body>
</soap:Envelope>"""

SOAP_ENVELOPE_CURS_ON_DATE = """<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <GetCursOnDate xmlns="http://web.cbr.ru/">
      <On_date>{on_date}</On_date>
    </GetCursOnDate>
  </soap12:Body>
</soap12:Envelope>"""

SOAP_ENVELOPE_LATEST_DATETIME = """<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <GetLatestDateTime xmlns="http://web.cbr.ru/" />
  </soap12:Body>
</soap12:Envelope>"""


async def fetch_key_rate_latest(window_days: int = 1) -> dict | None:
    """
    Возвращает последнюю запись вида:
    {'date': 'YYYY-MM-DD', 'key_rate': float}
    с кешированием на 2 дня
    """
    redis = await get_redis()
    cache_key = "cbr:key_rate"

    # Проверяем кеш
    try:
        cached = await redis.get(cache_key)
        if cached:
            logger.debug("CBR key rate from cache")
            data = json.loads(cached)
            # Конвертируем строку даты обратно в date
            data["date"] = date.fromisoformat(data["date"])
            return data
    except Exception as e:
        logger.warning(f"Redis get error for {cache_key}: {e}")

    end = date.today()
    start = end - timedelta(days=window_days)

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    body = SOAP_ENVELOPE_KEYRATE.format(from_dt=start.isoformat(), to_dt=end.isoformat())

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(SOAP_URL, headers=headers, content=body)
        resp.raise_for_status()
        xml = resp.text

    root = ET.fromstring(xml)

    # Ищем KeyRate, игнорируя namespace
    keyrates = None
    for elem in root.iter():
        if elem.tag.endswith("KeyRate"):
            keyrates = elem
            break

    if keyrates is None:
        logger.warning("Could not find KeyRate in CBR response")
        return None

    # Ищем все KR элементы (KeyRate rows)
    rows = []
    for elem in keyrates.iter():
        if elem.tag.endswith("KR"):
            rows.append(elem)

    if not rows:
        logger.warning("No KR rows found in KeyRate response")
        return None

    out = []
    for r in rows:
        dt_txt = None
        rate_txt = None

        for child in r:
            if child.tag.endswith("DT"):
                dt_txt = (child.text or "").strip()
            elif child.tag.endswith("Rate"):
                rate_txt = (child.text or "").strip()

        if not dt_txt or not rate_txt:
            continue
        # DT приходит с таймзоной, '2025-10-16T00:00:00'
        try:
            d = datetime.fromisoformat(dt_txt).date()
        except ValueError:
            continue
        try:
            rate = float(rate_txt.replace(",", "."))
        except ValueError:
            continue
        out.append((d, rate))

    if not out:
        return None
    out.sort(key=lambda x: x[0])
    last_date, last_rate = out[-1]
    result = {"date": last_date, "key_rate": last_rate}

    # Кешируем на 2 дня (172800 секунд)
    try:
        # Сериализуем в JSON (date -> string)
        cache_data = json.dumps({"date": last_date.isoformat(), "key_rate": last_rate})
        await redis.setex(cache_key, 172800, cache_data)
        logger.debug(f"CBR key rate cached: {last_rate}% on {last_date}")
    except Exception as e:
        logger.warning(f"Redis setex error for {cache_key}: {e}")

    return result


async def fetch_last_date_cbr() -> date:
    """Возвращает дату последнего обновления курсов ЦБ с кешированием (TTL 10 минут)."""
    redis = await get_redis()
    cache_key = "cbr:last_date"
    latest_date = date.today()

    # Проверяем кеш
    try:
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"CBR last date from cache: {cached}")
            return date.fromisoformat(cached)
    except Exception as e:
        logger.warning(f"Redis get error for {cache_key}: {e}")

    # Запрашиваем у ЦБ
    headers = {"Content-Type": "text/xml; charset=utf-8"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(SOAP_URL, headers=headers, content=SOAP_ENVELOPE_LATEST_DATETIME)
        resp.raise_for_status()
        xml = resp.text

    root = ET.fromstring(xml)
    # Ищем GetLatestDateTimeResult напрямую, игнорируя namespace
    latest_dt_txt = None
    for elem in root.iter():
        if elem.tag.endswith("GetLatestDateTimeResult"):
            latest_dt_txt = (elem.text or "").strip()
            break

    if not latest_dt_txt:
        logger.warning("Could not parse CBR last date from XML")
        return latest_date

    logger.debug(f"CBR returned last date: {latest_dt_txt}")
    try:
        latest_date = datetime.fromisoformat(latest_dt_txt).date()
    except ValueError as e:
        logger.warning(f"Could not parse date {latest_dt_txt}: {e}")
        pass

    # Кешируем на 10 минут (600 секунд)
    try:
        await redis.setex(cache_key, 600, latest_date.isoformat())
        logger.debug(f"CBR last date cached: {latest_date}")
    except Exception as e:
        logger.warning(f"Redis setex error for {cache_key}: {e}")

    return latest_date


async def fetch_exchanges_rate_on_date(on_date: date) -> list[CBRValuteItem]:
    """Возвращает список курсов валют на заданную дату с кешированием (TTL 1 час).

    Args:
        on_date (date): Дата, для которой нужно получить курсы валют.

    Returns:
        list[CBRValuteItem]: Список курсов валют на заданную дату.
    """
    redis = await get_redis()
    cache_key = f"cbr:rates:{on_date.isoformat()}"

    # Проверяем кеш
    try:
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"CBR rates from cache for {on_date}")
            # Десериализуем из JSON
            data = json.loads(cached)
            return [CBRValuteItem(**item) for item in data]
    except Exception as e:
        logger.warning(f"Redis get error for {cache_key}: {e}")

    # Запрашиваем у ЦБ
    headers = {"Content-Type": "text/xml; charset=utf-8"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            SOAP_URL, headers=headers, content=SOAP_ENVELOPE_CURS_ON_DATE.format(on_date=on_date.isoformat())
        )
        resp.raise_for_status()
        xml = resp.text

    root = ET.fromstring(xml)

    # Ищем ValuteData, игнорируя namespace
    valute_data = None
    for elem in root.iter():
        if elem.tag.endswith("ValuteData"):
            valute_data = elem
            break

    if valute_data is None:
        logger.warning(f"Could not find ValuteData in CBR response for {on_date}")
        return []

    valutes: list[CBRValuteItem] = []
    # Ищем все ValuteCursOnDate элементы
    for valute in valute_data.iter():
        if not valute.tag.endswith("ValuteCursOnDate"):
            continue

        rate = None
        name = None
        code = None

        for child in valute:
            if child.tag.endswith("VunitRate"):
                rate = child.text
            elif child.tag.endswith("Vname"):
                name = child.text
            elif child.tag.endswith("VchCode"):
                code = child.text

        if not rate or not name or not code:
            continue

        valutes.append(CBRValuteItem(rate=float(rate.replace(",", ".")), name=name.strip(), char_code=code.strip()))

    # Кешируем на 2 дня (172800 секунд)
    try:
        # Сериализуем в JSON
        cache_data = json.dumps([v.model_dump() for v in valutes])
        await redis.setex(cache_key, 172800, cache_data)
        logger.debug(f"CBR rates cached for {on_date}, {len(valutes)} currencies")
    except Exception as e:
        logger.warning(f"Redis setex error for {cache_key}: {e}")

    return valutes
