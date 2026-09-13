"""Free historical intraday data via Yahoo Finance — no API key, no Kite
subscription needed. Returns the same candle shape as data.py's Kite wrapper
(list of {date, open, high, low, close, volume} dicts) so backtest.py's core
loop doesn't care which source it came from.

Coverage/reliability is best-effort (Yahoo's NSE data, not the exchange
itself) — good enough to validate strategy logic against real price
history, not a substitute for real broker data before trading real money.

Yahoo caps how far back intraday intervals go: 60m ~730 days, 15m/30m/5m
~60 days, 1m ~7 days.
"""
import yfinance as yf


def fetch_candles(symbol: str, interval: str, period: str) -> list[dict]:
    """symbol: a Yahoo ticker, e.g. 'RELIANCE.NS' or the Nifty 50 index '^NSEI'.
    interval: '60m', '15m', '5m', etc. period: '5d', '30d', '60d', etc."""
    df = yf.Ticker(symbol).history(interval=interval, period=period)
    return [
        {
            "date": ts.to_pydatetime(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        }
        for ts, row in df.iterrows()
    ]
