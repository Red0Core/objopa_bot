import pytest
import asyncio
from typing import Any

# Helper function for mocking async returns
def async_return(result: Any):
    future = asyncio.Future()
    future.set_result(result)
    return future

@pytest.fixture
def mock_forex_data():
    return {
        "Meta Data": {
            "1. Information": "FX Daily Prices",
            "2. From Symbol": "USD",
            "3. To Symbol": "RUB"
        },
        "Time Series FX (Daily)": {
            "2025-04-05": {"4. close": "91.0000"},
            "2025-04-04": {"4. close": "90.5000"},
            "2025-03-29": {"4. close": "89.2000"},
            "2025-03-05": {"4. close": "88.0000"}
        }
    }

@pytest.fixture
def mock_coinmarketcap_data():
    from backend.models.markets import CoinData, QuoteData
    return [
        CoinData(
            name="Bitcoin",
            symbol="BTC",
            is_active=1,
            quote={
                "USD": QuoteData(
                    price=65000.0,
                    percent_change_1h=0.5,
                    percent_change_24h=2.3,
                    percent_change_7d=5.1,
                    percent_change_30d=15.2,
                    market_cap=1200000000000.0,
                    volume_24h=35000000000.0
                )
            }
        )
    ]

@pytest.fixture
def mock_exchange_data():
    return {"symbol": "BTCUSDT", "price": 65000.0}

@pytest.fixture
def mock_cbr_data():
    return {
        "USD": {"rate": 90.5, "diff": 0.3},
        "EUR": {"rate": 98.7, "diff": 0.1}
    }