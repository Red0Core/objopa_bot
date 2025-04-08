from pydantic import BaseModel


class HoroscopeResponse(BaseModel):
    sign: str
    emoji: str
    daily: str
    finance: str
