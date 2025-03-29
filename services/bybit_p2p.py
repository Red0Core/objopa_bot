from curl_cffi import AsyncSession
from dataclasses import dataclass
import traceback

@dataclass
class Offer:
    price: float
    nickname: str
    finish_num: int
    is_va: bool
    is_ba: bool
    min_amount: float = 0
    max_amount: float = 0
    available_amount: float = 0

def generate_categories_html_output(ranges: dict[str, list[Offer]]):
    html = "<b>📦 Лучшие предложения по покупке/продаже USDT:</b>\n"

    for label in ranges:
        html += f"\n<b>{label}</b>:\n"
        items = ranges[label]

        if not items:
            html += "— Нет подходящих офферов\n"
            continue

        for offer in items:
            # Improved badges with clearer distinction between merchant types
            if offer.is_ba:
                badge = "🔷"  # Business Account - самый лучший
            elif offer.is_va:
                badge = "🛡️"  # Verified Account - подтвержден Bybit
            else:
                badge = "👤"  # Обычный пользователь
                
            html += f"{badge} <i>{offer.nickname}</i>\n" \
                f"- <b>{offer.price:.2f} ₽</b>\n" \
                f"- <i>{offer.min_amount:,.0f}-{offer.max_amount:,.0f} ₽</i>\n" \
                f"- <b>{offer.available_amount:.2f} USDT доступно</b>\n" \
                f"- {offer.finish_num} сделок\n"

    return html

def generate_amount_html_output(offers: list[Offer], amount: float, is_fiat: bool = False) -> str:
    html = f"<b>📦 Лучшие предложения на {amount} {"₽" if is_fiat else "USDT"}:</b>\n"

    if not offers:
        html += "— Нет подходящих офферов\n"
        return html

    for offer in offers:
        # Improved badges with clearer distinction between merchant types
        if offer.is_ba:
            badge = "🔷"  # Business Account - самый лучший
        elif offer.is_va:
            badge = "🛡️"  # Verified Account - подтвержден Bybit
        else:
            badge = "👤"  # Обычный пользователь
        
        html += f"{badge} <i>{offer.nickname}</i>\n" \
                f"- <b>{offer.price:.2f} ₽</b>\n" \
                f"- <i>{offer.min_amount:,.0f}-{offer.max_amount:,.0f} ₽</i>\n" \
                f"- <b>{offer.available_amount:.2f} USDT доступно</b>\n" \
                f"- {offer.finish_num} сделок\n"
        
        if is_fiat:
            amount_output = amount / offer.price
            html += f"- <b>💵 {amount_output:.2f} USDT</b>\n"
        else:
            amount_output = amount * offer.price
            html += f"- <b>💵 {amount_output:.2f} ₽</b>\n"

    return html

def categorize_all_offers(data):
    categories = {
        "до 20K": [],
        "до 50K": [],
        "до 100K": [],
        "больше 100K": []
    }

    for item in data["result"]["items"]:
        try:
            min_amount = float(item["minAmount"])
            max_amount = float(item["maxAmount"])
            price = float(item["price"])
            finish_num = item.get("finishNum", 0)
            nickname = item.get("nickName", "Unknown")
            is_va = "VA" in item.get("authTag", [])
            is_ba = "BA" in item.get("authTag", [])
            available_amount = float(item["lastQuantity"])

            entry = Offer(price, nickname, finish_num, is_va, is_ba, min_amount, max_amount, available_amount)

            if min_amount <= 20000:
                categories["до 20K"].append(entry)
            elif min_amount <= 50000:
                categories["до 50K"].append(entry)
            elif min_amount <= 100000:
                categories["до 100K"].append(entry)
            else:
                categories["больше 100K"].append(entry)

        except Exception as e:
            print(f"Ошибка при обработке ордера: {traceback.format_exc()}")

    return categories

def get_offers_by_amount(data, amount: float, is_fiat: bool = False) -> list[Offer]:
    """
    Функция для категоризации офферов по исполняемому объему
    """
    offers: list[Offer] = []
    for item in data["result"]["items"]:
        try:
            min_amount = float(item["minAmount"])
            max_amount = float(item["maxAmount"])
            price = float(item["price"])
            finish_num = item.get("finishNum", 0)
            nickname = item.get("nickName", "Unknown")
            is_va = "VA" in item.get("authTag", [])
            is_ba = "BA" in item.get("authTag", [])
            available_amount = float(item["lastQuantity"])

            # В рублях, мы проверяем доступный объем в рублях
            if is_fiat:
                if min_amount <= amount <= max_amount and available_amount*price >= amount:
                    entry = Offer(price, nickname, finish_num, is_va, is_ba, min_amount, max_amount, available_amount)
                    offers.append(entry)
            # В USDT, мы проверяем доступный объем в USDT
            else:
                usdt_min_amount = min_amount / price
                usdt_max_amount = max_amount / price
                if usdt_min_amount <= amount <= usdt_max_amount and available_amount >= amount:
                    entry = Offer(price, nickname, finish_num, is_va, is_ba, min_amount, max_amount, available_amount)
                    offers.append(entry)

        except Exception as e:
            print(f"Ошибка при обработке ордера с объемом: {traceback.format_exc()}")

    return offers

def get_offers_by_valid_makers(offers: list[Offer]) -> list[Offer]:
    """
    Функция для фильтрации офферов по валидным мейкерам. Предполагает сортированный по цене вариант
    """
    offers_output = []
    ba_offer_added = False
    va_offer_added = False
    ga_offer_added = False
    for offer in offers:
        if offer.is_ba and not ba_offer_added:
            ba_offer_added = True
            offers_output.append(offer)
            break # Так как самый лучший и надежный вариант, то дальше нам искать не надо
        if offer.is_va and not va_offer_added:
            va_offer_added = True
            offers_output.append(offer)
            continue
        if not offer.is_ba and not offer.is_va and not ga_offer_added:
            ga_offer_added = True
            offers_output.append(offer)
            continue

    return offers_output

async def get_p2p_orders(is_buy: bool = True):
    headers = {
        'accept': 'application/json',
        'accept-language': 'en',
        'content-type': 'application/json;charset=UTF-8',
        'guid': '58485422-5be5-333d-7276-88f7c4eaadbe',
        'lang': 'en',
        'origin': 'https://www.bybit.com',
        'platform': 'PC',
        'priority': 'u=1, i',
        'referer': 'https://www.bybit.com/',
        'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Brave";v="134"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'sec-gpc': '1',
        'traceparent': '00-fad4c70470b21d3a42a55cf5a8eee443-2a3111470a83330a-01',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
    }

    json_data = {
        'tokenId': 'USDT',
        'currencyId': 'RUB',
        'payment': [
            '75',
            '377',
            '64',
            '581',
            '63',
            '379',
            '584',
            '585',
            '582',
        ],
        'side': '1' if is_buy else '0',
        'size': '200',
        'page': '1',
        'amount': '',
        'bulkMaker': False,
        'canTrade': True,
        'verificationFilter': 0,
        'sortType': 'TRADE_PRICE',
        'paymentPeriod': [],
        'itemRegion': 1,
    }

    async with AsyncSession() as s:
        s.headers.update(headers)
        req = await s.post("https://api2.bybit.com/fiat/otc/item/online", json=json_data)
        return req.json()

    return None

if __name__ == "__main__":
    import asyncio
    data = asyncio.run(get_p2p_orders(is_buy=True))
    categorized_data = categorize_all_offers(data)
    for label in categorized_data:
        categorized_data[label] = get_offers_by_valid_makers(categorized_data[label])
    html_output = generate_categories_html_output(categorized_data)
    print(html_output)

    offers = get_offers_by_amount(data, 40000, True)
    html_output = generate_amount_html_output(get_offers_by_valid_makers(offers), 40000, True)
    print(html_output)

    offers = get_offers_by_amount(data, 500, False)
    html_output = generate_amount_html_output(get_offers_by_valid_makers(offers), 500, False)
    print(html_output)
