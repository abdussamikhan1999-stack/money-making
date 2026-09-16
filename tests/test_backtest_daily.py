from datetime import datetime, timedelta

from backtest_daily import simulate_daily, walk_forward_daily
from daily_strategy import DonchianBreakoutStrategy
from strategy import Signal, Side


def bar(day, open_, high, low, close):
    return {"date": datetime(2020, 1, 1) + timedelta(days=day - 1),
            "open": open_, "high": high, "low": low, "close": close, "volume": 0}


def test_simulate_daily_enters_long_on_channel_breakout_and_stops_out():
    bars = [
        bar(1, 9.5, 10, 9, 9.5), bar(2, 9.5, 11, 9, 10), bar(3, 10, 10, 8, 9.5),  # 3-day channel: high 11, low 8
        bar(4, 9.5, 13, 12, 12.5),  # closes above 11 -> long @ 12.5, stop = 8
        bar(5, 12.5, 12.5, 7, 7.5),  # low breaches stop -> stopped out
    ]
    broker, risk = simulate_daily(bars, DonchianBreakoutStrategy(entry_period=3), capital=100_000)
    assert len(broker.trade_log) == 1
    trade = broker.trade_log[0]
    assert trade["side"] == "LONG"
    assert trade["entry"] == 12.5
    assert trade["exit"] == 8


def test_simulate_daily_no_trade_when_price_stays_inside_channel():
    bars = [bar(d, 9.5, 10, 9, 9.5) for d in range(1, 10)]
    broker, risk = simulate_daily(bars, DonchianBreakoutStrategy(entry_period=3), capital=100_000)
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
    broker, risk = simulate_daily(bars, DonchianBreakoutStrategy(entry_period=3, exit_period=2), capital=100_000)
    assert len(broker.trade_log) == 1
    trade = broker.trade_log[0]
    assert trade["side"] == "LONG"
    assert trade["entry"] == 12.5
    assert trade["exit"] == 14  # the exit-channel close, not the 8 hard stop
    assert trade["pnl"] > 0  # and the profit is NOT the tiny fixed +5 the old bug would have produced


def test_simulate_daily_resets_the_daily_loss_breaker_each_bar():
    """Regression test for a real bug: simulate_daily never called
    risk.reset_day(), so RiskManager's "daily" loss breaker (default 2% of
    capital, meant to reset every day) silently became a permanent
    cumulative-loss-since-inception halt instead — trading stopped for
    good after roughly 4 losing trades (4 x 0.5% risk each ~= 2%) and
    stayed halted for the rest of a run, however many years remained.
    Build enough repeated losing breakout-then-stop-loss cycles (well
    under the 10%-of-capital drawdown breaker, but well over the 2%
    "daily" threshold in cumulative terms) and confirm trading continues
    past that point."""
    bars = []
    day = 1
    for _ in range(8):  # 8 losing round-trips; the bug would have stopped this at ~4
        bars.append(bar(day, 9.5, 10, 9, 9.5)); day += 1
        bars.append(bar(day, 9.5, 10, 9, 9.5)); day += 1
        bars.append(bar(day, 9.5, 10, 9, 9.5)); day += 1  # 3-day channel: high 10, low 9
        bars.append(bar(day, 9.5, 12, 11.5, 12)); day += 1  # breaks out above 10 -> long @ 12, stop = 9
        bars.append(bar(day, 12, 12, 8, 8.5)); day += 1  # crashes through the stop -> loss

    broker, risk = simulate_daily(bars, DonchianBreakoutStrategy(entry_period=3), capital=100_000)
    assert len(broker.trade_log) == 8
    assert all(t["pnl"] < 0 for t in broker.trade_log)
    assert risk.drawdown_halted is False  # well under the (separate, correctly cumulative) 10% drawdown breaker


def test_walk_forward_daily_splits_into_two_fresh_halves():
    bars = [bar(d, 9.5, 10, 9, 9.5) for d in range(1, 21)]
    (b1, r1), (b2, r2) = walk_forward_daily(bars, lambda: DonchianBreakoutStrategy(entry_period=3), capital=100_000)
    assert r1.equity == 100_000
    assert r2.equity == 100_000


class _FakeCurrentAtrStrategy:
    """Minimal strategy exposing current_atr(), to test the profit-booking
    ATR-scaling wiring in simulate_daily() in isolation from
    ConnorsRSI2Strategy's own entry/exit rules (see CLAUDE.md's
    profit-booking-overlay finding: RSI2 itself is a bad fit for this, but
    the wiring mechanism is generic and worth testing on its own).
    stop_distance/atr are parameterized (defaults unchanged) so the same
    fixture also covers vol_size_cap's opposite case: a stop distance
    TIGHTER than the instrument's own ATR, where the cap should bind."""
    def __init__(self, stop_distance=10, atr=2.0):
        self._entered = False
        self._stop_distance = stop_distance
        self._atr = atr

    def push(self, bar) -> None:
        pass

    def check_entry(self, close):
        if self._entered:
            return None
        self._entered = True
        return Signal(Side.LONG, close, close - self._stop_distance, "fake entry")

    def check_exit(self, close, side) -> bool:
        return False  # never exits via strategy rule; only the trailing stop should end it

    def current_atr(self):
        return self._atr


def test_simulate_daily_profit_booking_tightens_stop_using_strategy_current_atr():
    bars = [
        bar(1, 100, 100, 100, 100),  # enters long @ 100, hard stop = 90
        bar(2, 100, 106, 100, 106),  # +6 profit = 3x the fake 2.0 ATR -> both triggers (1x, 2x ATR) fire
        bar(3, 100, 106, 96, 96),    # pulls back hard - the tightened stop should catch this well above 90
    ]
    broker, risk = simulate_daily(bars, _FakeCurrentAtrStrategy(), capital=100_000,
                                   breakeven_atr_mult=1.0, trail_atr_mult=2.0)
    assert len(broker.trade_log) == 1
    assert broker.trade_log[0]["exit"] > 90  # far above the original hard stop - profit was locked in


def test_simulate_daily_without_atr_mults_keeps_old_inf_inf_behavior():
    """Default (None, None) must reproduce the pre-existing behavior exactly:
    a strategy exposing current_atr() should NOT have its stop tightened
    unless both --breakeven-atr-mult and --trail-atr-mult are explicitly set."""
    bars = [
        bar(1, 100, 100, 100, 100),
        bar(2, 100, 106, 100, 106),  # would have tightened the stop well above 90 WITH profit-booking on
        bar(3, 100, 106, 96, 96),    # doesn't breach the original hard stop of 90 - stays open
        bar(4, 96, 96, 85, 85),      # now breaches the original hard stop
    ]
    broker, risk = simulate_daily(bars, _FakeCurrentAtrStrategy(), capital=100_000)
    assert len(broker.trade_log) == 1
    assert broker.trade_log[0]["exit"] == 90  # only the original hard stop, untouched


def test_vol_size_cap_shrinks_qty_when_atr_exceeds_stop_distance():
    """A tight 2-unit stop alone would size 250 units (risk_amount 500 /
    stop_distance 2), but the instrument's own ATR is 10 - vol_size_cap
    should take the smaller of the two (500 / 10 = 50)."""
    bars = [
        bar(1, 100, 100, 100, 100),  # enters long @ 100, tight stop = 98
        bar(2, 97, 97, 97, 97),      # breaches it
    ]
    broker, risk = simulate_daily(bars, _FakeCurrentAtrStrategy(stop_distance=2, atr=10),
                                   capital=100_000, vol_size_cap=True)
    assert len(broker.trade_log) == 1
    assert broker.trade_log[0]["qty"] == 50


def test_vol_size_cap_off_by_default_keeps_stop_based_sizing():
    bars = [
        bar(1, 100, 100, 100, 100),
        bar(2, 97, 97, 97, 97),
    ]
    broker, risk = simulate_daily(bars, _FakeCurrentAtrStrategy(stop_distance=2, atr=10), capital=100_000)
    assert len(broker.trade_log) == 1
    assert broker.trade_log[0]["qty"] == 250  # uncapped, old behavior unchanged
