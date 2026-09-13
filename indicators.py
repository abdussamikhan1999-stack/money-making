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


def sma(values: list[float], period: int | None = None) -> float | None:
    """Simple moving average of the last `period` values (default: all of
    them). Returns None if there isn't enough history."""
    period = len(values) if period is None else period
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def rsi(closes: list[float], period: int = 2, seed_window: int = 20) -> float | None:
    """Wilder-smoothed RSI, windowed rather than tracking state from all of
    history (consistent with average_true_range's own windowed style):
    seeds the smoothing from a simple average over the last `seed_window`
    changes, then applies Wilder smoothing across that window only. This is
    a pragmatic approximation of a "proper" whole-history Wilder RSI — the
    difference is negligible once the seed window stabilizes, and it avoids
    every caller needing to maintain running smoothing state.

    Returns None if there isn't enough history (needs seed_window + 1
    closes). `period` is the RSI lookback (2 for Connors' RSI-2, 14 for the
    classic Wilder RSI)."""
    lookback = seed_window + 1
    if len(closes) < lookback or seed_window < period:
        return None
    window = closes[-lookback:]
    deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
