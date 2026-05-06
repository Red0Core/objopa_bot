import asyncio
import traceback
from datetime import date, timedelta
from typing import Any

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from telegramify_markdown import markdownify

from core.config import BACKEND_ROUTE
from core.logger import logger

router = Router()


async def build_cbr_message(requested_codes: list[str] | None = None) -> str:
    """
    Формирует сообщение с курсами ЦБ РФ и ключевой ставкой.

    Args:
        requested_codes: Список кодов валют для отображения. Если None, использует дефолтные.

    Returns:
        HTML-форматированное сообщение с курсами
    """
    if requested_codes is None:
        requested_codes = ["USD", "EUR", "BYN", "CNY"]

    async with httpx.AsyncClient() as session:
        # Получаем последнюю дату
        last_date_response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/last-date")
        last_date_response.raise_for_status()
        last_date_str = last_date_response.json()["date"]

        last_date = date.fromisoformat(last_date_str)
        yesterday = last_date - timedelta(days=1)

        # Получаем курсы за последнюю дату и за вчера
        today_response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates?date={last_date.isoformat()}")
        yesterday_response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates?date={yesterday.isoformat()}")

        # Получаем ключевую ставку
        key_rate_response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/key-rate")

        today_response.raise_for_status()
        yesterday_response.raise_for_status()
        key_rate_response.raise_for_status()

        today_data = today_response.json()
        yesterday_data = yesterday_response.json()
        key_rate_data = key_rate_response.json()

        if not today_data.get("rates"):
            return "Нет данных для отображения"

        # Создаем словари для быстрого поиска
        today_rates = {item["char_code"]: item for item in today_data["rates"]}
        yesterday_rates = {item["char_code"]: item for item in yesterday_data.get("rates", [])}

        # Формируем сообщение
        output = f"💱 <b>Курсы ЦБ РФ на {last_date.strftime('%d.%m.%Y')}</b>\n\n"

        # Ключевая ставка
        if key_rate_data.get("key_rate") is not None:
            key_rate = key_rate_data["key_rate"]
            output += f"🏦 <b>Ключевая ставка:</b> <code>{key_rate:.2f}%</code>\n\n"

        found_currencies = []
        not_found = []

        for code in requested_codes:
            if code in today_rates:
                today_item = today_rates[code]
                today_rate = today_item["rate"]

                # Рассчитываем изменение
                change_str = ""
                if code in yesterday_rates:
                    yesterday_rate = yesterday_rates[code]["rate"]
                    diff = today_rate - yesterday_rate
                    if diff > 0:
                        change_str = f" (<code>+{diff:.4f}</code>)"
                    elif diff < 0:
                        change_str = f" (<code>{diff:.4f}</code>)"
                    else:
                        change_str = " (—)"

                output += f"<b>{code}</b>: <code>{today_rate:.4f}</code> ₽{change_str}\n"
                output += f"    <i>{today_item['name']}</i>\n"
                found_currencies.append(code)
            else:
                not_found.append(code)

        # Если есть ненайденные валюты
        if not_found:
            output += f"\n⚠️ Не найдено в ЦБ: {', '.join(not_found)}"

        return output


@router.message(Command("cbr"))
async def get_cbr_rates_handler(message: Message):
    """
    Отправляет курсы ЦБ РФ за сегодня с изменениями от вчера.
    По умолчанию: USD, EUR, BYN, CNY + ключевая ставка
    Можно указать дополнительные валюты: /cbr GBP JPY
    """
    logger.info(f"Отправляем курсы валют пользователю {message.from_user.id if message.from_user else 'неизвестный'}")

    # Дефолтные валюты
    default_codes = ["USD", "EUR", "BYN", "CNY"]

    # Парсим дополнительные валюты из команды
    additional_codes = []
    if message.text:
        parts = message.text.split()[1:]  # Skip /cbr
        additional_codes = [code.upper() for code in parts]

    # Объединяем дефолтные и дополнительные (без дубликатов)
    requested_codes = list(dict.fromkeys(default_codes + additional_codes))

    try:
        output = await build_cbr_message(requested_codes)
        await message.reply(output, parse_mode="html")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await message.reply("Ошибка: неверный запрос")
        else:
            await message.reply("Ошибка: не удалось получить данные")
    except Exception:
        logger.error(f"Ошибка при получении курсов ЦБ: {traceback.format_exc()}")
        await message.reply("Произошла ошибка при получении данных")


@router.message(Command("rate"))
async def convert_currency_handler(message: Message):
    """
    Конвертер валют через курсы ЦБ РФ.

    Примеры:
    /rate 5 USD
    /rate 5 USD RUB
    /rate 5 BYN USD RUB EUR

    Если валюты назначения не указаны, по дефолту конвертирует в RUB.
    """
    if not message.text:
        await message.reply(
            markdownify(
                "❌ Использование: /rate <сумма> <из валюты> <в валюту...>\n"
                "Пример: /rate 5 BYN USD RUB EUR"
            )
        )
        return

    parts = message.text.split()

    if len(parts) < 3:
        await message.reply(
            markdownify(
                "❌ Неверный формат!\n"
                "Использование: /rate <сумма> <из валюты> <в валюту...>\n"
                "Пример: /rate 5 BYN USD RUB EUR\n"
                "Если валюту назначения не указать: /rate 5 USD → RUB"
            )
        )
        return

    try:
        amount = float(parts[1].replace(",", "."))
        from_currency = parts[2].upper()
        to_currencies = [currency.upper() for currency in parts[3:]] or ["RUB"]
    except ValueError:
        await message.reply(markdownify("❌ Сумма должна быть числом!"))
        return

    # Убираем дубликаты, но сохраняем порядок
    to_currencies = list(dict.fromkeys(to_currencies))

    try:
        async with httpx.AsyncClient() as session:
            response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates")
            response.raise_for_status()
            data = response.json()

            if not data.get("rates"):
                await message.reply("Нет данных для конвертации")
                return

            rates_dict = {item["char_code"]: item for item in data["rates"]}

            if from_currency != "RUB" and from_currency not in rates_dict:
                await message.reply(markdownify(f"❌ Валюта {from_currency} не найдена в ЦБ РФ"))
                return

            not_found = [
                currency
                for currency in to_currencies
                if currency != "RUB" and currency not in rates_dict
            ]

            if not_found:
                await message.reply(
                    markdownify(f"❌ Валюты не найдены в ЦБ РФ: {', '.join(not_found)}")
                )
                return

            if from_currency == "RUB":
                amount_in_rub = amount
            else:
                amount_in_rub = amount * rates_dict[from_currency]["rate"]

            output = "💱 <b>Конвертация валют</b>\n\n"
            output += f"<code>{amount:.2f}</code> <b>{from_currency}</b>:\n"

            results: list[str] = []

            for to_currency in to_currencies:
                if to_currency == "RUB":
                    result = amount_in_rub
                else:
                    result = amount_in_rub / rates_dict[to_currency]["rate"]

                output += f"→ <code>{result:.2f}</code> <b>{to_currency}</b>\n"
                results.append(f"{result:.2f} {to_currency}")

            output += "\n"

            if from_currency != "RUB":
                output += f"Курс {from_currency}: {rates_dict[from_currency]['rate']:.4f} ₽\n"

            for to_currency in to_currencies:
                if to_currency != "RUB":
                    output += f"Курс {to_currency}: {rates_dict[to_currency]['rate']:.4f} ₽\n"

            await message.reply(output, parse_mode="html")

            logger.info(
                f"Конвертация: {amount} {from_currency} → {', '.join(results)}"
            )

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await message.reply("Ошибка: неверный запрос")
        else:
            await message.reply("Ошибка: не удалось получить данные")
    except Exception:
        logger.error(f"Ошибка при конвертации валют: {traceback.format_exc()}")
        await message.reply("Произошла ошибка при конвертации")

@router.message(Command("rub"))
async def get_forex_rub_rates_handler(message: Message):
    """
    Отправляет курсы USD и EUR с данными из Forex (изменения за 1/7/30 дней) + EUR из ЦБ РФ.
    """
    output = "💹 <b>Курсы валют к рублю</b>\n\n"

    try:
        async with httpx.AsyncClient() as session:

            async def fetch_currency_data(from_symbol: str, to_symbol: str) -> dict[str, Any]:
                response = await session.get(f"{BACKEND_ROUTE}/markets/forex/{from_symbol}/{to_symbol}")
                response.raise_for_status()
                return response.json()

            # Получаем USD/RUB и EUR/RUB из Forex + EUR из ЦБ
            forex_results = await asyncio.gather(fetch_currency_data("USD", "RUB"), fetch_currency_data("EUR", "RUB"))

            # Получаем EUR из ЦБ РФ
            cbr_response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates")
            cbr_response.raise_for_status()
            cbr_data = cbr_response.json()

            # Forex данные
            for data in forex_results:
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
                    f"<b>{symbol}/{market} (Forex):</b>\n"
                    f"Текущая цена: <code>{today:.2f} {market}</code>\n"
                    f"🔸 За 1 день: <code>{change_1d[0]:+.2f} ({change_1d[1]:+.2f}%)</code>\n"
                )
                if change_7d:
                    output += f"🔹 За 7 дней: <code>{change_7d[0]:+.2f} ({change_7d[1]:+.2f}%)</code>\n"
                if change_30d:
                    output += f"🔸 За 30 дней: <code>{change_30d[0]:+.2f} ({change_30d[1]:+.2f}%)</code>\n"

                output += "\n"

            # ЦБ РФ данные для EUR
            if cbr_data.get("rates"):
                cbr_rates = {item["char_code"]: item for item in cbr_data["rates"]}
                if "EUR" in cbr_rates:
                    eur_cbr = cbr_rates["EUR"]
                    output += (
                        f"<b>EUR/RUB (ЦБ РФ):</b>\n"
                        f"Курс: <code>{eur_cbr['rate']:.4f} RUB</code>\n"
                        f"    <i>{eur_cbr['name']}</i>\n"
                    )
                if "USD" in cbr_rates:
                    usd_cbr = cbr_rates["USD"]
                    output += (
                        f"\n<b>USD/RUB (ЦБ РФ):</b>\n"
                        f"Курс: <code>{usd_cbr['rate']:.4f} RUB</code>\n"
                        f"    <i>{usd_cbr['name']}</i>\n"
                    )

    except httpx.HTTPStatusError:
        output += "\n❌ Ошибка: не удалось получить данные"
        await message.reply(output, parse_mode="html")
        return
    except Exception as e:
        logger.error(f"Ошибка: {traceback.format_exc()}")
        await message.reply(f"❌ Ошибка: {e}")
        return

    await message.reply(output, parse_mode="html")
    logger.info(f"Успешно отправил курсы USD/EUR для {message.from_user.id if message.from_user else 'unknown'}")
