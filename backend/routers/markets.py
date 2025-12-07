from fastapi import APIRouter, HTTPException

from backend.models.markets import (
    CBRResponse,
    CoinmarketcapResponse,
    CoinmarketcapWhitelistRequest,
    ForexChange,
    ForexChanges,
    ForexResponse,
    P2PResponse,
    PriceResponse,
)
from backend.services.markets import alphavantage, bybit_p2p, cbr, coinmarketcap, exchanges
import datetime as dt

router = APIRouter(prefix="/markets", tags=["markets"])


# P2P Endpoints
@router.get("/p2p")
async def get_p2p_offers(is_buy: bool, amount: float, is_fiat: bool):
    """Get P2P offers filtered by amount and currency type."""
    try:
        data = await bybit_p2p.get_p2p_orders(is_buy=is_buy)
        if not data:
            raise HTTPException(status_code=500, detail={"error": "No P2P data available"})

        offers = bybit_p2p.get_offers_by_amount(data, amount, is_fiat)
        filtered_offers = bybit_p2p.get_only_best_offers_by_valid_makers(offers)

        return P2PResponse(
            offers=filtered_offers,
            html_output=bybit_p2p.generate_amount_html_output(filtered_offers, amount, is_fiat),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Service error: {e}"}) from e


# Forex Endpoints
@router.get("/forex/{base}/{quote}", response_model=ForexResponse)
async def get_forex_rates(base: str, quote: str = "RUB"):
    """Get currency exchange rates from Alpha Vantage."""
    data = await alphavantage.fetch_currency_data(base.upper(), quote.upper())
    today, yesterday, price_7d, price_30d = alphavantage.parse_currency_data(data)

    if today and yesterday:
        change_1d = alphavantage.calculate_change(today, yesterday)
        change_7d = alphavantage.calculate_change(today, price_7d) if price_7d else None
        change_30d = alphavantage.calculate_change(today, price_30d) if price_30d else None

        return ForexResponse(
            base=base.upper(),
            quote=quote.upper(),
            rate=today,
            changes=ForexChanges(
                day1=ForexChange(absolute=change_1d[0], percent=change_1d[1]),
                day7=ForexChange(absolute=change_7d[0], percent=change_7d[1]) if change_7d else None,
                day30=ForexChange(absolute=change_30d[0], percent=change_30d[1]) if change_30d else None,
            ),
        )
    raise HTTPException(status_code=500, detail={"error": "Failed to fetch forex data"})


# Crypto Endpoints
@router.get("/crypto/{symbol}")
async def get_crypto_price(symbol: str, amount: float | None = None) -> CoinmarketcapResponse:
    """Get cryptocurrency data from CoinMarketCap."""
    data = await coinmarketcap.get_coinmarketcap_data(symbol.upper())
    filtered_data = coinmarketcap.filter_tickers(data)

    return CoinmarketcapResponse(
        data=filtered_data,
        html_output=coinmarketcap.format_crypto_price(filtered_data, amount or 0.0),
    )


@router.post("/crypto/whitelist", status_code=201)
async def add_to_whitelist(request: CoinmarketcapWhitelistRequest):
    """Add a cryptocurrency to the whitelist."""
    for i in await coinmarketcap.get_coinmarketcap_data(request.symbol.upper()):
        if request.name == i.name:
            coinmarketcap.add_to_whitelist(request.symbol.upper(), request.name)
            return {"success": request.model_dump()}
    raise HTTPException(status_code=400, detail={"error": "Invalid symbol or name"})


# Exchange Price Endpoint
@router.get("/price/{symbol}", response_model=PriceResponse)
async def get_exchange_price(symbol: str):
    """Get cryptocurrency price from exchanges."""
    result = await exchanges.get_price_from_exchanges(symbol.upper())
    return result


# CBR Endpoints
@router.get("/cbr/rates", response_model=CBRResponse)
async def get_cbr_rates(date: dt.date | None = None):
    """Get Central Bank of Russia exchange rates. If no date is provided, return the latest."""
    rates = (
        await cbr.fetch_exchanges_rate_on_date(date)
        if date
        else await cbr.fetch_exchanges_rate_on_date(await cbr.fetch_last_date_cbr())
    )
    return CBRResponse(rates=rates)


@router.get("/cbr/last-date")
async def get_cbr_last_date():
    """Get the latest date for which CBR rates are available."""
    last_date = await cbr.fetch_last_date_cbr()
    return {"date": last_date.isoformat()}


@router.get("/cbr/key-rate")
async def get_cbr_key_rate():
    """Get the latest CBR key rate."""
    result = await cbr.fetch_key_rate_latest()
    if result is None:
        raise HTTPException(status_code=500, detail={"error": "Failed to fetch key rate"})
    return result
