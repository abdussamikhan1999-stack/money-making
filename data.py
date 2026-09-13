"""Historical/intraday candle fetch from Kite. Thin wrapper — all decision
logic lives in strategy.py, not here."""
from datetime import datetime, time

MARKET_OPEN = time(9, 15)


def fetch_candles(kite, instrument_token: int, interval: str,
                   from_dt: datetime, to_dt: datetime) -> list[dict]:
    """interval: one of Kite's historical intervals, e.g. 'minute', '15minute', '60minute', 'day'."""
    return kite.historical_data(instrument_token, from_dt, to_dt, interval)


def todays_candles(kite, instrument_token: int, interval: str) -> list[dict]:
    now = datetime.now()
    start = datetime.combine(now.date(), MARKET_OPEN)
    return fetch_candles(kite, instrument_token, interval, start, now)
