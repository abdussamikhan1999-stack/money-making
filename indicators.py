"""Pure technical indicators computed from candle dicts. No I/O.

Added for Carver-style volatility position sizing (risk.py's
volatility_position_size) — the source strategy thread itself pointed at
ATR ("ATR EXPLANATION" link) without ever specifying how to use it; this is
the standard definition, wired in as an option rather than a requirement.
"""


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def internal_bar_strength(bar: dict) -> float | None:
    """Internal Bar Strength: where today's close fell within TODAY's own
    high-low range (0 = closed at the low, 1 = at the high). Returns None
    on a zero-range bar. Promoted here (was a private helper duplicated
    inline in probe_ibs.py) once a second caller needed it."""
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return None
    return (bar["close"] - bar["low"]) / rng


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


def stdev(values: list[float], period: int | None = None) -> float | None:
    """Population standard deviation of the last `period` values (matches
    Pine Script's `stdev`, which divides by N not N-1 — needed to reproduce
    LazyBear's Squeeze Momentum Indicator's Bollinger Band width exactly).
    Returns None if there isn't enough history."""
    period = len(values) if period is None else period
    if len(values) < period or period <= 0:
        return None
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((v - mean) ** 2 for v in window) / period
    return variance ** 0.5


def highest(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return max(values[-period:])


def lowest(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return min(values[-period:])


def linreg(values: list[float]) -> float | None:
    """Value of the ordinary-least-squares best-fit line at the LAST point
    of `values` (Pine Script's `linreg(source, length, 0)` — the length is
    just len(values), since callers already pass a fixed-size window). Pure
    closed-form OLS, no numpy: x = 0..n-1, fitted value at x = n-1."""
    n = len(values)
    if n < 2:
        return None
    sum_x = n * (n - 1) / 2
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6
    sum_y = sum(values)
    sum_xy = sum(i * v for i, v in enumerate(values))
    denom = n * sum_x2 - sum_x ** 2
    if denom == 0:
        return None
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return intercept + slope * (n - 1)


def chaikin_money_flow(candles: list[dict], period: int = 20) -> float | None:
    """Chaikin Money Flow: each day's money-flow-volume (where the close sat
    within that day's own high-low range, weighted by that day's volume)
    summed over `period` days and normalized by total volume in the window.
    >0 means net buying pressure, <0 net selling. Needs `volume` in each
    candle dict alongside high/low/close. Returns None if there isn't
    enough history, or every bar's volume in the window is zero (e.g. an
    index with no real traded volume)."""
    if len(candles) < period:
        return None
    window = candles[-period:]
    total_volume = sum(c["volume"] for c in window)
    if total_volume == 0:
        return None
    mfv_sum = 0.0
    for c in window:
        day_range = c["high"] - c["low"]
        if day_range == 0:
            continue
        mfm = ((c["close"] - c["low"]) - (c["high"] - c["close"])) / day_range
        mfv_sum += mfm * c["volume"]
    return mfv_sum / total_volume


def amihud_illiq(candles: list[dict], period: int = 21) -> float | None:
    """Amihud (2002) illiquidity measure: mean daily |return| / dollar
    volume over the trailing `period` days -- the standard price-impact-
    per-rupee-of-volume proxy behind the illiquidity risk premium (higher
    ILLIQ = harder to trade without moving the price = compensated with a
    higher expected return). Needs `period + 1` candles (each day's return
    needs the PRIOR day's close, the same "one extra bar" requirement
    `on_balance_volume` above already has). Days with zero/negative volume
    or a non-positive prior close are skipped rather than raising (yfinance
    reports zero volume for index symbols, e.g. `^NSEI` -- ILLIQ is
    undefined for a security with no traded volume, same situation
    `chaikin_money_flow` already handles for the same root cause). Returns
    None if there isn't enough history, or every day in the window is
    skippable."""
    if len(candles) < period + 1:
        return None
    window = candles[-(period + 1):]
    ratios = []
    for i in range(1, len(window)):
        prev_close = window[i - 1]["close"]
        close = window[i]["close"]
        volume = window[i]["volume"]
        if prev_close <= 0 or volume <= 0:
            continue
        dollar_volume = close * volume
        ratios.append(abs((close - prev_close) / prev_close) / dollar_volume)
    return sum(ratios) / len(ratios) if ratios else None


def on_balance_volume(candles: list[dict], period: int = 20) -> float | None:
    """Windowed On-Balance Volume: net signed volume (an up day adds its
    volume, a down day subtracts it, a flat day contributes nothing) over
    the last `period` day-over-day changes — windowed rather than the
    classic running cumulative total since the start of all history
    (consistent with average_true_range/rsi's own windowed style in this
    module), so its sign is comparable across different time windows
    instead of drifting with wherever the cumulative sum happens to have
    started. Needs `period + 1` candles (period changes need period+1
    closes). Returns None if there isn't enough history."""
    if len(candles) < period + 1:
        return None
    window = candles[-(period + 1):]
    obv = 0.0
    for i in range(1, len(window)):
        if window[i]["close"] > window[i - 1]["close"]:
            obv += window[i]["volume"]
        elif window[i]["close"] < window[i - 1]["close"]:
            obv -= window[i]["volume"]
    return obv


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


def ema_update(prev_ema: float, price: float, period: int) -> float:
    """One step of the standard recursive EMA update. Unlike this file's
    other indicators (rsi/average_true_range/stdev), which recompute fresh
    from a bounded trailing window every call, an EMA's whole point is that
    older bars never fully drop out — recomputing it from a bounded window
    each call would be a different (approximate) indicator, not a windowed
    version of the same one. So callers seed the first value themselves
    (typically an SMA over `period` bars) then call this once per
    subsequent bar, keeping the running value as their own state (see
    MACDStrategy in daily_strategy.py)."""
    k = 2 / (period + 1)
    return price * k + prev_ema * (1 - k)
