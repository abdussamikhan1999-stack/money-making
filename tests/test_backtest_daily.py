from datetime import datetime

from backtest_daily import simulate_daily, walk_forward_daily


def bar(day, open_, high, low, close):
    return {"date": datetime(2020, 1, day), "open": open_, "high": high, "low": low, "close": close, "volume": 0}


def test_simulate_daily_enters_long_on_channel_breakout_and_stops_out():
    bars = [
        bar(1, 9.5, 10, 9, 9.5), bar(2, 9.5, 11, 9, 10), bar(3, 10, 10, 8, 9.5),  # 3-day channel: high 11, low 8
        bar(4, 9.5, 13, 12, 12.5),  # closes above 11 -> long @ 12.5, stop = 8
        bar(5, 12.5, 12.5, 7, 7.5),  # low breaches stop -> stopped out
    ]
    broker, risk = simulate_daily(bars, capital=100_000, entry_period=3)
    assert len(broker.trade_log) == 1
    trade = broker.trade_log[0]
    assert trade["side"] == "LONG"
    assert trade["entry"] == 12.5
    assert trade["exit"] == 8


def test_simulate_daily_no_trade_when_price_stays_inside_channel():
    bars = [bar(d, 9.5, 10, 9, 9.5) for d in range(1, 10)]
    broker, risk = simulate_daily(bars, capital=100_000, entry_period=3)
    assert broker.trade_log == []


def test_simulate_daily_exits_via_shorter_channel_not_hard_stop():
    """The actual bug fix: a position must be able to run well past a tiny
    fixed-point profit (unlike strategy.py's TrailingStopManager defaults,
    deliberately disabled here) and instead exit only via the hard
    structural stop or the shorter exit-channel breakdown."""
    bars = [
        bar(1, 9.5, 10, 9, 9.5), bar(2, 9.5, 11, 9, 10), bar(3, 10, 10, 8, 9.5),  # entry channel: high 11, low 8
        bar(4, 9.5, 13, 12, 12.5),  # breaks out -> long @ 12.5, hard stop = 8
        bar(5, 12.5, 20, 15, 18),   # runs up hugely — a tiny fixed trail would have exited near 12.5+5 long ago
        bar(6, 18, 22, 19, 20),
        bar(7, 20, 20, 14, 14),     # closes below the 2-day exit channel's low (15) -> exits here, not at the 8 hard stop
    ]
    broker, risk = simulate_daily(bars, capital=100_000, entry_period=3, exit_period=2)
    assert len(broker.trade_log) == 1
    trade = broker.trade_log[0]
    assert trade["side"] == "LONG"
    assert trade["entry"] == 12.5
    assert trade["exit"] == 14  # the exit-channel close, not the 8 hard stop
    assert trade["pnl"] > 0  # and the profit is NOT the tiny fixed +5 the old bug would have produced


def test_walk_forward_daily_splits_into_two_fresh_halves():
    bars = [bar(d, 9.5, 10, 9, 9.5) for d in range(1, 21)]
    (b1, r1), (b2, r2) = walk_forward_daily(bars, capital=100_000, entry_period=3)
    assert r1.equity == 100_000
    assert r2.equity == 100_000
