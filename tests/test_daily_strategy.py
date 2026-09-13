from daily_strategy import DonchianBreakoutStrategy
from strategy import Side


def test_no_signal_before_window_fills():
    s = DonchianBreakoutStrategy(entry_period=5)
    for h, l in [(10, 9), (11, 10), (10, 9), (11, 10)]:
        s.push(h, l)
    assert s.check_entry(close=12) is None  # only 4 days pushed, needs 5


def test_long_breakout_above_prior_window_high():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(h, l)
    sig = s.check_entry(close=12)  # above the 3-day high of 11
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == 12
    assert sig.stop_loss == 8  # the window's low


def test_short_breakdown_below_prior_window_low():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(h, l)
    sig = s.check_entry(close=7)  # below the 3-day low of 8
    assert sig is not None
    assert sig.side is Side.SHORT
    assert sig.entry_price == 7
    assert sig.stop_loss == 11  # the window's high


def test_no_signal_when_close_stays_inside_channel():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(h, l)
    assert s.check_entry(close=9.5) is None


def test_todays_own_bar_excluded_from_its_own_check():
    """No lookahead: check_entry must compare against the window BEFORE
    today's push(), not including it."""
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(h, l)
    # a huge high today would move the channel if included in its own check
    sig = s.check_entry(close=12)
    assert sig is not None
    assert sig.stop_loss == 8  # still the OLD window's low, not today's


def test_reset_clears_window():
    s = DonchianBreakoutStrategy(entry_period=3)
    for h, l in [(10, 9), (11, 9), (10, 8)]:
        s.push(h, l)
    s.reset()
    assert s.check_entry(close=100) is None
