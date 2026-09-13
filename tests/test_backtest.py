from datetime import datetime

from backtest import simulate, walk_forward


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

    broker, risk = simulate(h1, m15, intraday, capital=100_000, m15_filter=False)

    assert len(broker.trade_log) == 1
    trade = broker.trade_log[0]
    assert trade["side"] == "SHORT"
    assert trade["entry"] == 100
    assert trade["exit"] == 105
    assert trade["pnl"] == -500  # (100-105) * qty(100), a stopped-out loss
    assert risk.drawdown_halted is False


def test_simulate_resets_session_across_days():
    d1 = datetime(2026, 9, 1, 9, 15)
    d2 = datetime(2026, 9, 2, 9, 15)
    h1 = [candle(d1, 100, 100, 100, 100), candle(d2, 200, 200, 200, 200)]
    broker, risk = simulate(h1, [], [], capital=100_000)
    assert broker.trade_log == []  # no intraday data fed, just verifying no crash across a day boundary


def test_simulate_trips_drawdown_breaker_across_many_losing_days():
    """Reproduces (in miniature) the real finding: a strategy that keeps
    losing a small amount every day should eventually get permanently
    halted by cumulative drawdown, not just reset and keep going forever."""
    h1 = []
    intraday = []
    for day in range(1, 30):
        dt = datetime(2026, 9, day, 10, 15) if day <= 28 else datetime(2026, 10, day - 28, 10, 15)
        h1.append(candle(dt, 100, 100, 100, 100))
        intraday.append(candle(dt, 100, 105, 100, 100))               # arm + fire short @ 100
        intraday.append(candle(dt.replace(minute=30), 100, 106, 100, 105))  # stopped out for a loss

    broker, risk = simulate(h1, [], intraday, capital=100_000)
    # each loss is ~500 (0.5% of 100k); 10% drawdown (the RiskManager default) trips after ~20 losing trades
    assert risk.drawdown_halted is True
    assert len(broker.trade_log) < 29  # stopped taking new trades before exhausting all 29 days


def test_walk_forward_splits_into_two_independent_halves():
    h1 = [candle(datetime(2026, 9, d, 9, 15), 100, 100, 100, 100) for d in range(1, 11)]
    (in_broker, in_risk), (out_broker, out_risk) = walk_forward(h1, [], [], capital=100_000)
    # both halves get their own fresh capital baseline
    assert in_risk.equity == 100_000
    assert out_risk.equity == 100_000
