from datetime import date, datetime, timedelta

from probe_ibs_rotation import simulate


def _series(symbol_prices: dict, n_days: int = 10) -> dict:
    """symbol_prices: {symbol: (low_close, high_close, flat_price_or_None)}.
    If flat_price_or_None is given, the symbol's close is pinned at that
    price every day (used to force an exact, predictable share count under
    whole-share rounding) instead of drifting low->high like the other
    symbols (drift makes a symbol more "oversold" -> more likely picked)."""
    start = date(2026, 1, 1)
    series = {}
    for sym, (lo, hi, flat) in symbol_prices.items():
        candles = []
        for i in range(n_days):
            close = flat if flat is not None else lo + (hi - lo) * (i / (n_days - 1))
            candles.append({"date": datetime.combine(start + timedelta(days=i), datetime.min.time()),
                             "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 0})
        series[sym] = candles
    return series


def test_whole_shares_false_is_unchanged_default_behavior():
    series = _series({"A": (10, 10.5, None), "B": (10, 15, None)})
    entry_date, exit_date = date(2026, 1, 5), date(2026, 1, 9)
    r_default = simulate([entry_date, exit_date], series, top_k=2, lookback=5)
    r_explicit = simulate([entry_date, exit_date], series, top_k=2, lookback=5, whole_shares=False)
    assert r_default == r_explicit


def test_whole_shares_skips_a_pick_too_expensive_for_its_capital_slice():
    """Two affordable names (A, B) plus one priced above its whole capital
    slice (C, pinned at 99999) -- under whole_shares, C is unaffordable
    (0 whole shares at 100/3 per slice) and should be silently skipped
    rather than assumed tradable at a fractional size."""
    series = _series({"A": (10, 10.5, None), "B": (10, 15, None), "C": (10, 20, 99_999.0)})
    entry_date, exit_date = date(2026, 1, 5), date(2026, 1, 9)
    r = simulate([entry_date, exit_date], series, top_k=3, lookback=5, capital=100.0, whole_shares=True)
    assert r["n_months"] == 1
    assert r["months"][0]["n"] == 2


def test_whole_shares_never_trades_more_than_continuous_notional_return():
    """Rounding down to whole shares can only leave capital idle, never
    invest more than the continuous-notional version -- so its net P&L
    should never exceed the unrounded version's on the same data."""
    series = _series({"A": (10, 10.5, None), "B": (10, 15, None)})
    entry_date, exit_date = date(2026, 1, 5), date(2026, 1, 9)
    r_cont = simulate([entry_date, exit_date], series, top_k=2, lookback=5, capital=1_000.0)
    r_whole = simulate([entry_date, exit_date], series, top_k=2, lookback=5, capital=1_000.0, whole_shares=True)
    assert r_whole["total_net"] <= r_cont["total_net"]
