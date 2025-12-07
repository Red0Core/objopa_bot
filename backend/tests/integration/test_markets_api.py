from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.models.markets import Offer


@pytest.fixture
def mock_bybit_response():  # Переименовал с mock_bybit_orders на mock_bybit_response для согласованности
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


@pytest.fixture
def mock_offers() -> list[Offer]:
    """Sample processed Offer objects"""
    return [
        Offer(
            price=95.5,
            nickname="User1",
            finish_num=100,
            is_va=True,
            is_ba=False,
            payment_types=["Тинек", "Сбер"],
            min_amount=1000,
            max_amount=30000,
            available_amount=500,
        ),
        Offer(
            price=96.0,
            nickname="User2",
            finish_num=200,
            is_va=False,
            is_ba=True,
            payment_types=["Райф", "СБП"],
            min_amount=25000,
            max_amount=80000,
            available_amount=1000,
        ),
    ]


@pytest.mark.integration
@patch("backend.services.markets.bybit_p2p.get_p2p_orders")
@patch("backend.services.markets.bybit_p2p.get_offers_by_amount")
@patch("backend.services.markets.bybit_p2p.get_only_best_offers_by_valid_makers")
@patch("backend.services.markets.bybit_p2p.generate_amount_html_output")
def test_p2p_endpoint_success(
    mock_generate_output,  # Аргументы должны идти в обратном порядке декораторов
    mock_best_offers,
    mock_offers_by_amount,
    mock_p2p_orders,
    client: TestClient,  # client должен идти после mock-объектов
    mock_bybit_response,
    mock_offers,
):
    """Test successful P2P endpoint request"""
    # Setup mocks
    mock_p2p_orders.return_value = mock_bybit_response
    mock_offers_by_amount.return_value = mock_offers
    mock_best_offers.return_value = mock_offers
    mock_generate_output.return_value = "<b>Лучшие предложения:</b>\n- User1: 95.5 ₽\n- User2: 96.0 ₽"

    # Make request
    data = {"amount": 5000.0, "is_buy": True, "is_fiat": True}
    response = client.get("/markets/p2p", params=data)

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert "offers" in data
    assert "html_output" in data
    assert len(data["offers"]) == 2
    assert data["html_output"] == "<b>Лучшие предложения:</b>\n- User1: 95.5 ₽\n- User2: 96.0 ₽"

    # Verify service calls
    mock_p2p_orders.assert_called_once_with(is_buy=True)
    mock_offers_by_amount.assert_called_once_with(mock_bybit_response, 5000.0, True)
    mock_best_offers.assert_called_once_with(mock_offers)
    mock_generate_output.assert_called_once_with(mock_offers, 5000.0, True)


@pytest.mark.integration
@patch("backend.services.markets.bybit_p2p.get_p2p_orders")
@patch("backend.services.markets.bybit_p2p.get_offers_by_amount")
@patch("backend.services.markets.bybit_p2p.get_only_best_offers_by_valid_makers")
@patch("backend.services.markets.bybit_p2p.generate_amount_html_output")
def test_p2p_endpoint_no_offers(
    mock_generate_output,
    mock_best_offers,
    mock_offers_by_amount,
    mock_p2p_orders,
    client: TestClient,
    mock_bybit_response,
):
    """Test P2P endpoint when no offers match criteria"""
    # Setup mocks
    mock_p2p_orders.return_value = mock_bybit_response
    mock_offers_by_amount.return_value = []
    mock_best_offers.return_value = []
    mock_generate_output.return_value = "<b>Нет подходящих предложений</b>"

    # Make request
    data = {
        "amount": 500000.0,  # Higher than any available offer
        "is_buy": True,
        "is_fiat": True,
    }
    response = client.get("/markets/p2p", params=data)

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert "offers" in data
    assert "html_output" in data
    assert len(data["offers"]) == 0
    assert data["html_output"] == "<b>Нет подходящих предложений</b>"


@pytest.mark.integration
@patch("backend.services.markets.bybit_p2p.get_p2p_orders")
def test_p2p_endpoint_service_error(mock_p2p_orders, client: TestClient):
    """Test P2P endpoint when API service fails"""
    # Setup mock to throw exception
    mock_p2p_orders.side_effect = Exception("Service unavailable")

    # Make request - используем явные параметры вместо словаря
    response = client.get("/markets/p2p?amount=5000&is_fiat=true&is_buy=true")

    # Verify response code
    assert response.status_code == 500

    # Проверяем структуру ответа об ошибке
    error_data = response.json()

    # Проверка может быть разной в зависимости от вашего формата ошибок
    if isinstance(error_data, dict) and "detail" in error_data:
        # Standard FastAPI error format
        assert "detail" in error_data
        if isinstance(error_data["detail"], dict):
            assert "error" in error_data["detail"]
        else:
            assert "error" in error_data["detail"].lower() or "service" in error_data["detail"].lower()
    else:
        # Custom error format
        assert "error" in error_data or "message" in error_data


@pytest.mark.integration
@patch("backend.services.markets.bybit_p2p.get_p2p_orders")
@patch("backend.services.markets.bybit_p2p.get_offers_by_amount")
@patch("backend.services.markets.bybit_p2p.get_only_best_offers_by_valid_makers")
@patch("backend.services.markets.bybit_p2p.generate_amount_html_output")
def test_p2p_endpoint_sell_mode(
    mock_generate_output,
    mock_best_offers,
    mock_offers_by_amount,
    mock_p2p_orders,
    client: TestClient,
    mock_bybit_response,
    mock_offers,
):
    """Test P2P endpoint in sell mode (is_buy=False)"""
    # Setup mocks
    mock_p2p_orders.return_value = mock_bybit_response
    mock_offers_by_amount.return_value = mock_offers
    mock_best_offers.return_value = mock_offers
    mock_generate_output.return_value = "<b>Лучшие предложения для продажи:</b>\n- User1: 95.5 ₽\n- User2: 96.0 ₽"

    # Make request with is_buy=False
    data = {"amount": 5000.0, "is_buy": False, "is_fiat": True}
    response = client.get("/markets/p2p", params=data)

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert "offers" in data

    # Verify service calls
    mock_p2p_orders.assert_called_once_with(is_buy=False)
    mock_offers_by_amount.assert_called_once_with(mock_bybit_response, 5000.0, True)


# Fixtures для новых тестов
@pytest.fixture
def mock_alphavantage_response():
    """Мок ответа от Alpha Vantage API"""
    return {
        "Meta Data": {
            "1. Information": "Forex Daily Prices",
            "2. From Symbol": "USD",
            "3. To Symbol": "RUB",
        },
        "Time Series FX (Daily)": {
            "2025-04-05": {"4. close": "92.5"},
            "2025-04-04": {"4. close": "92.0"},
            "2025-03-29": {"4. close": "91.5"},
            "2025-03-06": {"4. close": "90.0"},
        },
    }


@pytest.fixture
def mock_cbr_response():
    """Мок ответа от ЦБ РФ API - список валют с курсами"""
    from backend.models.markets import CBRValuteItem

    return [
        CBRValuteItem(rate=92.5, name="Доллар США", char_code="USD"),
        CBRValuteItem(rate=99.8, name="Евро", char_code="EUR"),
        CBRValuteItem(rate=1.25, name="Юань", char_code="CNY"),
    ]


@pytest.fixture
def mock_exchange_response():
    """Мок ответа от бирж"""
    return {"symbol": "BTCUSDT", "price": 65000.0}


# Тест для эндпоинта forex
@pytest.mark.integration
@patch("backend.services.markets.alphavantage.fetch_currency_data")
@patch("backend.services.markets.alphavantage.parse_currency_data")
@patch("backend.services.markets.alphavantage.calculate_change")
def test_forex_endpoint_success(
    mock_calculate_change,
    mock_parse_data,
    mock_fetch_data,
    client: TestClient,
    mock_alphavantage_response,
):
    """Test forex endpoint happy path"""
    # Setup mocks
    mock_fetch_data.return_value = mock_alphavantage_response
    mock_parse_data.return_value = (92.5, 92.0, 91.5, 90.0)  # today, yesterday, 7d, 30d
    mock_calculate_change.side_effect = [
        (0.5, 0.54),  # 1d change
        (1.0, 1.09),  # 7d change
        (2.5, 2.78),  # 30d change
    ]

    # Make request
    response = client.get("/markets/forex/USD/RUB")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["base"] == "USD"
    assert data["quote"] == "RUB"
    assert data["rate"] == 92.5
    assert data["changes"]["day1"]["absolute"] == 0.5
    assert data["changes"]["day7"]["absolute"] == 1.0
    assert data["changes"]["day30"]["absolute"] == 2.5

    # Verify calls
    mock_fetch_data.assert_called_once_with("USD", "RUB")
    mock_parse_data.assert_called_once_with(mock_alphavantage_response)


# Тест для эндпоинта CBR
@pytest.mark.integration
@patch("backend.services.markets.cbr.fetch_last_date_cbr")
@patch("backend.services.markets.cbr.fetch_exchanges_rate_on_date")
def test_cbr_endpoint_success(mock_fetch_rates, mock_fetch_last_date, client: TestClient, mock_cbr_response):
    """Test CBR rates endpoint happy path"""
    from datetime import date

    # Setup mocks
    mock_fetch_last_date.return_value = date(2025, 10, 15)
    mock_fetch_rates.return_value = mock_cbr_response

    # Make request (без параметра date - получаем последнюю дату)
    response = client.get("/markets/cbr/rates")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert "rates" in data
    assert len(data["rates"]) == 3

    # Проверяем структуру первой валюты
    usd_rate = next(r for r in data["rates"] if r["char_code"] == "USD")
    assert usd_rate["rate"] == 92.5
    assert usd_rate["name"] == "Доллар США"
    assert usd_rate["char_code"] == "USD"

    # Проверяем EUR
    eur_rate = next(r for r in data["rates"] if r["char_code"] == "EUR")
    assert eur_rate["rate"] == 99.8
    assert eur_rate["name"] == "Евро"

    # Verify calls
    mock_fetch_last_date.assert_called_once()
    mock_fetch_rates.assert_called_once_with(date(2025, 10, 15))


@pytest.mark.integration
@patch("backend.services.markets.cbr.fetch_exchanges_rate_on_date")
def test_cbr_endpoint_with_custom_date(mock_fetch_rates, client: TestClient, mock_cbr_response):
    """Test CBR rates endpoint with custom date parameter"""
    from datetime import date

    # Setup mock
    mock_fetch_rates.return_value = mock_cbr_response

    # Make request с конкретной датой
    response = client.get("/markets/cbr/rates?date=2025-10-10")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert "rates" in data
    assert len(data["rates"]) == 3

    # Verify call с правильной датой
    mock_fetch_rates.assert_called_once_with(date(2025, 10, 10))


@pytest.mark.integration
@patch("backend.services.markets.cbr.fetch_last_date_cbr")
@patch("backend.services.markets.cbr.fetch_exchanges_rate_on_date")
def test_cbr_endpoint_empty_rates(mock_fetch_rates, mock_fetch_last_date, client: TestClient):
    """Test CBR rates endpoint when no rates available"""
    from datetime import date

    # Setup mocks to return empty list
    mock_fetch_last_date.return_value = date(2025, 10, 15)
    mock_fetch_rates.return_value = []

    # Make request
    response = client.get("/markets/cbr/rates")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert "rates" in data
    assert len(data["rates"]) == 0


# Тест для эндпоинта price (exchanges)
@pytest.mark.integration
@patch("backend.services.markets.exchanges.get_price_from_exchanges")
def test_exchanges_endpoint_success(mock_get_price, client: TestClient, mock_exchange_response):
    """Test exchange price endpoint happy path"""
    # Setup mock
    mock_get_price.return_value = mock_exchange_response

    # Make request
    response = client.get("/markets/price/BTC")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["price"] == 65000.0

    # Verify call
    mock_get_price.assert_called_once_with("BTC")


# Тесты для ошибок
@pytest.mark.integration
@patch("backend.services.markets.alphavantage.fetch_currency_data")
@patch("backend.services.markets.alphavantage.parse_currency_data")
def test_forex_endpoint_error(mock_parse_data, mock_fetch_data, client: TestClient):
    """Test forex endpoint when data parsing fails"""
    # Setup mocks to return incomplete data
    mock_fetch_data.return_value = {"Error Message": "Invalid API call"}
    mock_parse_data.return_value = (None, None, None, None)

    # Make request
    response = client.get("/markets/forex/INVALID/RUB")

    # Verify response
    assert response.status_code == 500
    error = response.json()
    assert "detail" in error


@pytest.mark.integration
@patch("backend.services.markets.cbr.fetch_last_date_cbr")
@patch("backend.services.markets.cbr.fetch_exchanges_rate_on_date")
def test_cbr_endpoint_error(mock_fetch_rates, mock_fetch_last_date, client: TestClient):
    """Test CBR endpoint when API fails"""
    from datetime import date

    # Setup mocks to throw exception
    mock_fetch_last_date.return_value = date(2025, 10, 15)
    mock_fetch_rates.side_effect = Exception("CBR API unavailable")

    # Make request
    response = client.get("/markets/cbr/rates")

    # Verify response - должна быть ошибка 500
    assert response.status_code == 500
    data = response.json()
    assert "detail" in data


@pytest.mark.integration
@patch("backend.services.markets.exchanges.get_price_from_exchanges")
def test_exchanges_endpoint_error(mock_get_price, client: TestClient):
    """Test exchange price endpoint when exchanges are unavailable"""
    # Setup mock to return error
    mock_get_price.return_value = {
        "symbol": None,
        "price": None,
        "error": "Symbol not found or exchanges unavailable",
    }

    # Make request
    response = client.get("/markets/price/UNKNOWN")

    # Response should still be 200 but with error in data
    assert response.status_code == 200
    data = response.json()
    assert data["error"] == "Symbol not found or exchanges unavailable"
    assert data["price"] is None
