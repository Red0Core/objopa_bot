from pydantic import BaseModel
from typing import Any, Sequence

# Bybit P2P Models
class Offer(BaseModel):
    price: float
    nickname: str
    finish_num: int
    is_va: bool
    is_ba: bool
    payment_types: Sequence[str]
    min_amount: float
    max_amount: float
    available_amount: float

class P2PResponse(BaseModel):
    offers: Sequence[Offer]
    html_output: str
    error: str | None = None

# Forex Models
class ForexChange(BaseModel):
    absolute: float
    percent: float

class ForexChanges(BaseModel):
    day1: ForexChange | None = None
    day7: ForexChange | None = None
    day30: ForexChange | None = None

class ForexResponse(BaseModel):
    base: str
    quote: str
    rate: float
    changes: ForexChanges
    error: str | None = None

# Coinmarketcap Models
class QuoteData(BaseModel):
    price: float | None
    percent_change_1h: float
    percent_change_24h: float
    percent_change_7d: float
    percent_change_30d: float
    market_cap: float | None
    volume_24h: float

class CoinData(BaseModel):
    name: str = "N/A"
    symbol: str = "N/A"
    is_active: int
    quote: dict[str, QuoteData]

class CoinmarketcapResponse(BaseModel):
    data: Sequence[CoinData]
    html_output: str
    
class CoinmarketcapWhitelistRequest(BaseModel):
    symbol: str
    name: str

# Exchange Price Model
class PriceResponse(BaseModel):
    symbol: str | None = None
    price: float | None = None
    error: str | None = None

# CBR Models
class RateItem(BaseModel):
    rate: float
    diff: float

class CBRResponse(BaseModel):
    rates: dict[str, RateItem | str]
    html_output: str