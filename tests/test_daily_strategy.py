from daily_strategy import (
    DonchianBreakoutStrategy, ConnorsRSI2Strategy, ThreeBarBreakoutStrategy, SqueezeMomentumStrategy,
    VolumeConfirmationStrategy,
)
from strategy import Side


def bar(high, low, close, open_=None, volume=0):
    return {"open": open_ if open_ is not None else close, "high": high, "low": low, "close": close, "volume": volume}


# --- DonchianBreakoutStrategy ---

def test_no_signal_before_window_fills():
    s = DonchianBreakoutStrategy(entry_period=5)
    for h, l in [(10, 9), (11, 10), (10, 9), (11, 10)]:
        s.push(bar(h, l, l))
    assert s.check_entry(close=12) is None  # only 4 days pushed, needs 5


def test_long_breakout_above_prior_window_high():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(bar(h, l, l))
    sig = s.check_entry(close=12)  # above the 3-day high of 11
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == 12
    assert sig.stop_loss == 8  # the window's low


def test_short_breakdown_below_prior_window_low():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(bar(h, l, l))
    sig = s.check_entry(close=7)  # below the 3-day low of 8
    assert sig is not None
    assert sig.side is Side.SHORT
    assert sig.entry_price == 7
    assert sig.stop_loss == 11  # the window's high


def test_no_signal_when_close_stays_inside_channel():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(bar(h, l, l))
    assert s.check_entry(close=9.5) is None


def test_todays_own_bar_excluded_from_its_own_check():
    """No lookahead: check_entry must compare against the window BEFORE
    today's push(), not including it."""
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(bar(h, l, l))
    sig = s.check_entry(close=12)
    assert sig is not None
    assert sig.stop_loss == 8  # still the OLD window's low, not today's


def test_reset_clears_window():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(bar(h, l, l))
    s.reset()
    assert s.check_entry(close=100) is None


# --- ConnorsRSI2Strategy ---

def _build_uptrend_rsi2(days=210):
    """A gentle, steady uptrend long enough to fill the 200-day trend SMA."""
    s = ConnorsRSI2Strategy(trend_period=200, atr_period=14, rsi_period=2)
    price = 100.0
    for _ in range(days):
        price += 0.5
        s.push(bar(price + 0.3, price - 0.3, price))
    return s, price


def test_no_entry_before_trend_window_fills():
    s = ConnorsRSI2Strategy(trend_period=200)
    for _ in range(50):
        s.push(bar(101, 99, 100))
    assert s.check_entry(close=100) is None


def test_long_entry_needs_oversold_rsi_and_price_above_trend_sma():
    s, last_price = _build_uptrend_rsi2()
    # a sharp one-day dip while still above the long-term trend SMA
    dip = last_price - 10
    sig = s.check_entry(close=dip)
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == dip
    assert sig.stop_loss < dip  # ATR-based stop below entry for a long


def test_no_long_entry_when_rsi_not_oversold():
    s, last_price = _build_uptrend_rsi2()
    # today continues the gentle uptrend -> RSI(2) nowhere near oversold
    assert s.check_entry(close=last_price + 0.5) is None


def test_exit_true_once_price_crosses_back_above_exit_sma_for_a_long():
    s = ConnorsRSI2Strategy(trend_period=5, exit_sma_period=3)
    for c in [100, 101, 102, 101, 100]:
        s.push(bar(c + 1, c - 1, c))
    # exit_sma (last 3 closes [102,101,100]) = 101; trend_sma (all 5) = 100.8
    # no-exit zone is trend_sma <= close <= exit_sma
    assert s.check_exit(close=105, side=Side.LONG) is True  # above exit_sma
    assert s.check_exit(close=100.9, side=Side.LONG) is False  # inside the no-exit zone


def test_exit_true_when_trend_filter_flips_against_a_long():
    s = ConnorsRSI2Strategy(trend_period=3, exit_sma_period=1)
    for c in [100, 100, 100]:
        s.push(bar(c + 1, c - 1, c))
    # trend SMA = 100; a close well below it should force an exit even if
    # the exit_sma condition alone wouldn't have fired
    assert s.check_exit(close=90, side=Side.LONG) is True


# --- ThreeBarBreakoutStrategy ---

def _build_threebar_with_compression(bar1_close, bar2_close, bar2_high=101, bar2_low=99):
    """13 stable bars (high=101/low=99/close=100, true range 2 each) to fill
    the 14-period ATR window, then bar1 and bar2 with the given closes -
    keeping high/low at the same 101/99 level (bar2 overridden if given)
    means every true range stays exactly 2, so ATR is exactly 2 and stop/
    target math is exact rather than approximate."""
    s = ThreeBarBreakoutStrategy(atr_period=14)
    for _ in range(13):
        s.push(bar(101, 99, 100))
    s.push(bar(101, 99, bar1_close))
    s.push(bar(bar2_high, bar2_low, bar2_close))
    return s


def test_no_signal_before_atr_window_fills():
    s = ThreeBarBreakoutStrategy(atr_period=14)
    for _ in range(10):
        s.push(bar(101, 99, 100))
    assert s.check_entry(close=110) is None


def test_long_breakout_requires_compression_then_close_above_both_priors():
    s = _build_threebar_with_compression(bar1_close=100.2, bar2_close=100.3)
    assert s.current_atr() == 2.0
    sig = s.check_entry(close=105)  # above both 100.2 and 100.3
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == 105
    assert sig.stop_loss == 98  # bar2's low (99) - 0.5 * ATR(2)


def test_short_breakdown_requires_compression_then_close_below_both_priors():
    s = _build_threebar_with_compression(bar1_close=100.2, bar2_close=100.3)
    sig = s.check_entry(close=95)  # below both 100.2 and 100.3
    assert sig is not None
    assert sig.side is Side.SHORT
    assert sig.entry_price == 95
    assert sig.stop_loss == 102  # bar2's high (101) + 0.5 * ATR(2)


def test_no_signal_when_bar1_and_bar2_are_not_compressed():
    # bar1/bar2 closes 2.8 apart, vs the 0.5 * ATR(2) = 1.0 compression cap
    s = _build_threebar_with_compression(bar1_close=100.2, bar2_close=103.0)
    assert s.check_entry(close=110) is None


def test_no_signal_when_close_does_not_clear_both_priors():
    s = _build_threebar_with_compression(bar1_close=100.2, bar2_close=100.3)
    assert s.check_entry(close=100.25) is None  # between bar1 and bar2, clears neither


def test_check_exit_true_once_target_is_reached():
    s = _build_threebar_with_compression(bar1_close=100.2, bar2_close=100.3)
    sig = s.check_entry(close=105)
    # risk = 105 - 98 = 7; target = 105 + 2.5 * 7 = 122.5 (default target_r_multiple)
    assert s.check_exit(close=122.4, side=sig.side) is False
    assert s.check_exit(close=122.5, side=sig.side) is True


def test_check_exit_true_after_max_hold_days_even_without_target():
    s = ThreeBarBreakoutStrategy(atr_period=14, max_hold_days=3)
    for _ in range(13):
        s.push(bar(101, 99, 100))
    s.push(bar(101, 99, 100.2))
    s.push(bar(101, 99, 100.3))
    sig = s.check_entry(close=105)
    assert sig is not None
    s.push(bar(106, 104, 106))  # day 1 in trade, nowhere near target, not timed out
    assert s.check_exit(close=106, side=sig.side) is False
    s.push(bar(106, 104, 106))  # day 2
    assert s.check_exit(close=106, side=sig.side) is False
    s.push(bar(106, 104, 106))  # day 3 - hits max_hold_days
    assert s.check_exit(close=106, side=sig.side) is True


def test_reset_clears_state():
    s = _build_threebar_with_compression(bar1_close=100.2, bar2_close=100.3)
    s.check_entry(close=105)
    s.reset()
    assert s.check_entry(close=110) is None  # ATR window emptied, needs refilling


# --- SqueezeMomentumStrategy ---

def _feed_flat_then_breakout(s):
    """8 flat days (constant close=100, high/low=101/99 -> BB stdev is 0
    while ATR/KC width is nonzero, so BB sits entirely inside KC: squeeze
    ON) followed by an accelerating uptrend (102, 105, then 110) that widens
    BB enough to expand past KC on the third step -> squeeze fires, exactly
    on the close=110 call. Every call to check_entry() during the flat
    phase must still happen (not just push()) since sqzOn tracking is
    updated inside _compute(), which only runs from check_entry/check_exit."""
    for _ in range(8):
        assert s.check_entry(close=100) is None
        s.push(bar(101, 99, 100))
    assert s.check_entry(close=102) is None
    s.push(bar(103, 101, 102))
    assert s.check_entry(close=105) is None
    s.push(bar(106, 104, 105))
    return s.check_entry(close=110)


def test_no_signal_before_window_fills():
    s = SqueezeMomentumStrategy(length=5)
    for _ in range(5):
        s.push(bar(101, 99, 100))
    assert s.check_entry(close=100) is None  # 5 pushed, needs length+1=6


def test_no_signal_without_a_prior_squeeze():
    """A pure uptrend from the very first bar never has a squeeze to fire
    off of (prev_sqz_on starts None/False) - momentum alone isn't enough."""
    s = SqueezeMomentumStrategy(length=5)
    fired = False
    price = 100
    for _ in range(10):
        price *= 1.03
        if s.check_entry(close=price):
            fired = True
        s.push(bar(price + 1, price - 1, price))
    assert fired is False


def test_squeeze_fires_long_on_breakout_from_compression():
    s = SqueezeMomentumStrategy(length=5, stop_atr_multiple=2.0)
    sig = _feed_flat_then_breakout(s)
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == 110
    # stop = entry - stop_atr_multiple * ATR, using the SAME ATR the signal
    # itself was built from (current_atr() reflects the just-completed
    # _compute() call inside check_entry)
    assert sig.stop_loss == 110 - 2.0 * s.current_atr()


def test_squeeze_fires_short_on_breakdown_from_compression():
    s = SqueezeMomentumStrategy(length=5, stop_atr_multiple=2.0)
    for _ in range(8):
        assert s.check_entry(close=100) is None
        s.push(bar(101, 99, 100))
    assert s.check_entry(close=98) is None
    s.push(bar(99, 97, 98))
    assert s.check_entry(close=95) is None
    s.push(bar(96, 94, 95))
    sig = s.check_entry(close=90)
    assert sig is not None
    assert sig.side is Side.SHORT
    assert sig.entry_price == 90
    assert sig.stop_loss == 90 + 2.0 * s.current_atr()


def test_check_exit_true_once_momentum_flips_against_a_long():
    s = SqueezeMomentumStrategy(length=5, stop_atr_multiple=2.0, max_hold_days=50)
    sig = _feed_flat_then_breakout(s)
    s.push(bar(111, 109, 110))
    assert s.check_exit(close=90, side=sig.side) is False  # one sharp drop, not yet reflected
    s.push(bar(91, 89, 90))
    assert s.check_exit(close=70, side=sig.side) is True  # momentum has now flipped negative


def test_check_exit_true_after_max_hold_days_even_without_momentum_flip():
    s = SqueezeMomentumStrategy(length=5, stop_atr_multiple=2.0, max_hold_days=3)
    sig = _feed_flat_then_breakout(s)
    s.push(bar(111, 109, 110))  # day 1 in trade
    assert s.check_exit(close=111, side=sig.side) is False
    s.push(bar(112, 110, 111))  # day 2
    assert s.check_exit(close=112, side=sig.side) is False
    s.push(bar(113, 111, 112))  # day 3 - hits max_hold_days
    assert s.check_exit(close=113, side=sig.side) is True


def test_reset_clears_squeeze_state():
    s = SqueezeMomentumStrategy(length=5)
    sig = _feed_flat_then_breakout(s)
    assert sig is not None
    s.reset()
    assert s.check_entry(close=110) is None  # window emptied, needs refilling


# --- VolumeConfirmationStrategy ---

def _feed_bullish_days(s, n=4, start=105, step=5):
    """n up-days, close at each day's own high, real volume every day -
    fills CMF/OBV/ATR positive across the board."""
    price = start
    for _ in range(n):
        s.push(bar(price, price - 5, price, volume=100))
        price += step


def test_no_signal_before_window_fills():
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3)
    _feed_bullish_days(s, n=3)  # needs obv_period+1=4 / atr_period+1=4, only 3 pushed
    assert s.check_entry(close=125) is None


def test_long_entry_when_cmf_and_obv_both_positive():
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3, stop_atr_multiple=2.0)
    _feed_bullish_days(s, n=4)  # 4 up-days, close at the high every day, real volume
    sig = s.check_entry(close=125)
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == 125
    assert sig.stop_loss == 125 - 2.0 * s.current_atr()


def test_no_entry_when_volume_is_zero_throughout():
    """An index with no real traded volume (yfinance reports 0) - CMF can't
    be computed, so this strategy must never fire on it."""
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3)
    price = 105
    for _ in range(4):
        s.push(bar(price, price - 5, price, volume=0))
        price += 5
    assert s.check_entry(close=125) is None


def test_short_entry_when_cmf_and_obv_both_negative():
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3, stop_atr_multiple=2.0)
    price = 120
    for _ in range(4):
        s.push(bar(price + 5, price, price, volume=100))  # close at the LOW every day
        price -= 5
    sig = s.check_entry(close=95)
    assert sig is not None
    assert sig.side is Side.SHORT
    assert sig.entry_price == 95
    assert sig.stop_loss == 95 + 2.0 * s.current_atr()


def test_check_exit_true_once_cmf_and_obv_both_flip_negative():
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3, max_hold_days=50)
    _feed_bullish_days(s, n=4)
    sig = s.check_entry(close=125)
    assert sig is not None
    # feed 4 down-days, close at the low every day, to flip CMF/OBV negative
    price = 125
    for _ in range(3):
        s.push(bar(price, price - 5, price - 5, volume=100))
        price -= 5
        if s.check_exit(close=price, side=sig.side):
            break
    else:
        assert False, "expected check_exit to fire once CMF/OBV both turned negative"


def test_check_exit_true_after_max_hold_days_even_without_signal_flip():
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3, max_hold_days=2)
    _feed_bullish_days(s, n=4)
    sig = s.check_entry(close=125)
    assert sig is not None
    s.push(bar(130, 125, 130, volume=100))  # day 1 in trade, still bullish
    assert s.check_exit(close=130, side=sig.side) is False
    s.push(bar(135, 130, 135, volume=100))  # day 2 - hits max_hold_days
    assert s.check_exit(close=135, side=sig.side) is True


def test_reset_clears_volume_state():
    s = VolumeConfirmationStrategy(cmf_period=3, obv_period=3, atr_period=3)
    _feed_bullish_days(s, n=4)
    sig = s.check_entry(close=125)
    assert sig is not None
    s.reset()
    assert s.check_entry(close=125) is None  # window emptied, needs refilling
