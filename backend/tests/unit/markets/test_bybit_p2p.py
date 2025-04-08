from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.markets import Offer
from backend.services.markets.bybit_p2p import (
    PAYMENT_TYPE,
    BybitP2PResponse,
    categorize_all_offers,
    generate_amount_html_output,
    generate_categories_html_output,
    get_offers_by_amount,
    get_only_best_offers_by_valid_makers,
    get_p2p_orders,
)


@pytest.fixture
def sample_response() -> BybitP2PResponse:
    return {
        "result": {
            "items": [
                {
                    "minAmount": "1000",
                    "maxAmount": "30000",
                    "price": "95.5",
                    "finishNum": 100,
                    "nickName": "User1",
                    "authTag": ["VA"],
                    "lastQuantity": "500",
                    "payments": [75, 377],
                },
                {
                    "minAmount": "25000",
                    "maxAmount": "80000",
                    "price": "96.0",
                    "finishNum": 200,
                    "nickName": "User2",
                    "authTag": ["BA"],
                    "lastQuantity": "1000",
                    "payments": [64, 379],
                },
                {
                    "minAmount": "60000",
                    "maxAmount": "150000",
                    "price": "96.5",
                    "finishNum": 150,
                    "nickName": "User3",
                    "authTag": ["GA"],
                    "lastQuantity": "2000",
                    "payments": [582, 382],
                },
                {
                    "minAmount": "120000",
                    "maxAmount": "250000",
                    "price": "97.0",
                    "finishNum": 300,
                    "nickName": "User4",
                    "authTag": ["VA"],
                    "lastQuantity": "3000",
                    "payments": [584, 585],
                },
            ]
        }
    }


def test_categorize_all_offers(sample_response):
    result = categorize_all_offers(sample_response)

    assert len(result) == 4
    assert len(result["до 20K"]) == 1
    assert len(result["до 50K"]) == 1
    assert len(result["до 100K"]) == 1
    assert len(result["больше 100K"]) == 1

    assert result["до 20K"][0].nickname == "User1"
    assert result["до 50K"][0].nickname == "User2"
    assert result["до 100K"][0].nickname == "User3"
    assert result["больше 100K"][0].nickname == "User4"


def test_get_offers_by_amount_fiat(sample_response):
    # Test with fiat=True
    result = get_offers_by_amount(sample_response, 27000, True)

    assert len(result) == 2
    assert result[0].nickname == "User1"

    # Test with amount out of range
    result = get_offers_by_amount(sample_response, 300000, True)
    assert len(result) == 0

    # Test with insufficient available amount
    modified_response = sample_response.copy()
    modified_response["result"]["items"][0]["lastQuantity"] = "200"
    modified_response["result"]["items"][1]["lastQuantity"] = "200"  # Reduced available amount
    result = get_offers_by_amount(modified_response, 25000, True)
    assert len(result) == 0  # Because 200 USDT * 96.0 < 25000 RUB


def test_get_offers_by_amount_usdt(sample_response):
    # Test with fiat=False (USDT)
    result = get_offers_by_amount(sample_response, 400, False)

    assert len(result) == 1  # User1 and User2 have enough available USDT, but MaxAmount is 30000
    assert {offer.nickname for offer in result} == {"User2"}

    # Test with amount out of range in USDT
    result = get_offers_by_amount(sample_response, 2800, False)
    assert len(result) == 0


def test_get_offers_by_valid_makers():
    offers = [
        Offer(
            price=95.0,
            nickname="Regular1",
            finish_num=100,
            is_va=False,
            is_ba=False,
            payment_types=("Сбер",),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
        Offer(
            price=95.5,
            nickname="Verified1",
            finish_num=200,
            is_va=True,
            is_ba=False,
            payment_types=("Сбер",),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
        Offer(
            price=96.0,
            nickname="Business1",
            finish_num=300,
            is_va=False,
            is_ba=True,
            payment_types=("Сбер",),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
        Offer(
            price=96.5,
            nickname="Regular2",
            finish_num=150,
            is_va=False,
            is_ba=False,
            payment_types=("Тинек",),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
        Offer(
            price=97.0,
            nickname="Verified2",
            finish_num=250,
            is_va=True,
            is_ba=False,
            payment_types=("Тинек",),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
    ]

    # Should return 3 offers: 1 VA, 1 BA, and 1 regular
    result = get_only_best_offers_by_valid_makers(offers)
    assert len(result) == 3
    assert result[0].nickname == "Regular1"

    # Without BA, should return one VA and one regular
    offers_without_ba = [o for o in offers if not o.is_ba]
    result = get_only_best_offers_by_valid_makers(offers_without_ba)
    assert len(result) == 2
    assert "Verified1" in [o.nickname for o in result]
    assert "Regular1" in [o.nickname for o in result]


def test_generate_categories_html_output(sample_response):
    categories = categorize_all_offers(sample_response)
    html = generate_categories_html_output(categories)

    assert "📦 Лучшие предложения по покупке/продаже USDT" in html
    assert "до 20K" in html
    assert "до 50K" in html
    assert "до 100K" in html
    assert "больше 100K" in html
    assert "User1" in html
    assert "User2" in html
    assert "User3" in html
    assert "User4" in html


def test_generate_amount_html_output():
    offers = [
        Offer(
            price=95.0,
            nickname="Regular1",
            finish_num=100,
            is_va=False,
            is_ba=False,
            payment_types=("Сбер", "Тинек"),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
        Offer(
            price=96.0,
            nickname="Business1",
            finish_num=300,
            is_va=False,
            is_ba=True,
            payment_types=("Сбер",),
            min_amount=1000,
            max_amount=10000,
            available_amount=100,
        ),
    ]

    # Test with fiat=True
    html_fiat = generate_amount_html_output(offers, 5000, True)
    assert "5000 ₽" in html_fiat
    assert "Regular1" in html_fiat
    assert "Business1" in html_fiat
    assert "52.63" in html_fiat  # 5000/95.0
    assert "52.08" in html_fiat  # 5000/96.0

    # Test with fiat=False
    html_crypto = generate_amount_html_output(offers, 50, False)
    assert "50" in html_crypto
    assert "4750.00" in html_crypto  # 50*95.0
    assert "4800.00" in html_crypto  # 50*96.0

    # Test with empty offers
    html_empty = generate_amount_html_output([], 5000, True)
    assert "Нет подходящих офферов" in html_empty


@pytest.mark.asyncio
@patch("backend.services.markets.bybit_p2p.AsyncSession")
async def test_get_p2p_orders(mock_session):
    # Setup mock response
    mock_response = AsyncMock()
    mock_response.json.return_value = {"result": {"items": []}}

    # Setup session mock
    session_instance = MagicMock()
    session_instance.post.return_value = mock_response
    mock_session.return_value.__aenter__.return_value = session_instance

    # Test buy orders
    result = await get_p2p_orders(is_buy=True)
    assert result == {"result": {"items": []}}
    session_instance.post.assert_called_once()

    # Check that the right side was sent
    called_with = session_instance.post.call_args[1]["json"]
    assert called_with["side"] == "1"  # Buy

    # Reset mock
    session_instance.post.reset_mock()

    # Test sell orders
    result = await get_p2p_orders(is_buy=False)
    assert result == {"result": {"items": []}}
    session_instance.post.assert_called_once()

    # Check that the right side was sent
    called_with = session_instance.post.call_args[1]["json"]
    assert called_with["side"] == "0"  # Sell


def test_payment_type_mapping():
    assert PAYMENT_TYPE[75] == "Тинек"
    assert PAYMENT_TYPE[377] == "Сбер"
    assert PAYMENT_TYPE[382] == "СБП"

    # Test unknown payment type
    unknown_id = 999
    assert unknown_id not in PAYMENT_TYPE
