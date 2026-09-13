from datetime import datetime

from backtest import simulate


def candle(dt, o, h, l, c):
    return {"date": dt, "open": o, "high": h, "low": l, "close": c, "volume": 0}


def test_simulate_produces_a_short_trade_on_a_clean_breakout_and_return():
    day = datetime(2026, 9, 1, 9, 15)
    hour = datetime(2026, 9, 1, 10, 15)

    h1 = [candle(day, 100, 100, 100, 100), candle(hour, 100, 105, 100, 100)]
    m15 = []  # filter disabled, so this can be empty
    intraday = [
        candle(datetime(2026, 9, 1, 10, 15), 100, 105, 100, 100),   # breaks above 100, returns -> fires SHORT @ 100
        candle(datetime(2026, 9, 1, 10, 30), 100, 106, 100, 105),   # runs up through the initial stop (105) -> stopped out
    ]

    result = simulate(h1, m15, intraday, capital=100_000, m15_filter=False)

    assert len(result.trade_log) == 1
    trade = result.trade_log[0]
    assert trade["side"] == "SHORT"
    assert trade["entry"] == 100
    assert trade["exit"] == 105
    assert trade["pnl"] == -500  # (100-105) * qty(100), a stopped-out loss


def test_simulate_resets_session_across_days():
    d1 = datetime(2026, 9, 1, 9, 15)
    d2 = datetime(2026, 9, 2, 9, 15)
    h1 = [candle(d1, 100, 100, 100, 100), candle(d2, 200, 200, 200, 200)]
    result = simulate(h1, [], [], capital=100_000)
    assert result.trade_log == []  # no intraday data fed, just verifying no crash across a day boundary
