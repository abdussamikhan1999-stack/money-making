"""Pure technical indicators computed from candle dicts. No I/O.

Added for Carver-style volatility position sizing (risk.py's
volatility_position_size) — the source strategy thread itself pointed at
ATR ("ATR EXPLANATION" link) without ever specifying how to use it; this is
the standard definition, wired in as an option rather than a requirement.
"""


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def average_true_range(candles: list[dict], period: int = 14) -> float | None:
    """Simple (unsmoothed) ATR over the last `period` candles, each a
    {high, low, close, ...} dict in chronological order. Returns None if
    there isn't enough history yet to compute a full window."""
    if len(candles) < period + 1:
        return None
    window = candles[-(period + 1):]
    trs = [
        true_range(window[i - 1]["close"], window[i]["high"], window[i]["low"])
        for i in range(1, len(window))
    ]
    return sum(trs) / len(trs)
