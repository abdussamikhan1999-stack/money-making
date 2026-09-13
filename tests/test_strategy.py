from strategy import HighLowOpenStrategy, TrailingStopManager, Side


def test_no_signal_before_any_bar_open():
    s = HighLowOpenStrategy()
    assert s.on_price(100) is None


def test_short_fires_on_breakout_and_return():
    s = HighLowOpenStrategy()
    s.on_bar_open(100)
    assert s.on_price(105) is None  # arms, doesn't fire yet
    sig = s.on_price(100)  # falls back through the line
    assert sig is not None
    assert sig.side is Side.SHORT
    assert sig.entry_price == 100
    assert sig.stop_loss == 105  # day high at moment of entry


def test_long_fires_on_breakdown_and_return():
    s = HighLowOpenStrategy()
    s.on_bar_open(100)
    assert s.on_price(95) is None
    sig = s.on_price(100)
    assert sig is not None
    assert sig.side is Side.LONG
    assert sig.entry_price == 100
    assert sig.stop_loss == 95


def test_no_signal_without_prior_breakout():
    s = HighLowOpenStrategy()
    s.on_bar_open(100)
    # price never went above/below the line, just sits on it
    assert s.on_price(100) is None
    assert s.on_price(100) is None


def test_one_shot_per_level_blocks_repeat_fire():
    s = HighLowOpenStrategy()
    s.on_bar_open(100)
    s.on_price(105)
    first = s.on_price(100)
    assert first is not None
    # break out and return again at the SAME level — should not re-fire
    s.on_price(105)
    second = s.on_price(100)
    assert second is None


def test_new_higher_open_moves_line_and_rearms():
    s = HighLowOpenStrategy()
    s.on_bar_open(100)
    s.on_price(105)
    s.on_price(100)  # fires short at 100
    s.on_bar_open(110)  # a later H1 bar opens higher — line moves
    assert s.highest_open == 110
    assert s.on_price(115) is None  # arms at the new level
    sig = s.on_price(110)
    assert sig is not None
    assert sig.entry_price == 110


def test_m15_filter_blocks_entry_without_confirmation():
    s = HighLowOpenStrategy(m15_filter=True)
    s.on_bar_open(100)
    s.on_price(105)
    # M15 open not beyond the line -> filtered out
    assert s.on_price(100, m15_open=99) is None
    # still armed; a later touch WITH confirmation should fire
    s.on_price(106)
    sig = s.on_price(100, m15_open=101)
    assert sig is not None
    assert sig.side is Side.SHORT


def test_reset_session_clears_state():
    s = HighLowOpenStrategy()
    s.on_bar_open(100)
    s.on_price(105)
    s.reset_session()
    assert s.highest_open is None
    assert s.lowest_open is None
    assert s.on_price(105) is None  # no line yet, can't fire


def test_trailing_stop_moves_to_breakeven_then_trails_long():
    t = TrailingStopManager(entry_price=100, initial_stop=95, side=Side.LONG,
                             breakeven_trigger=5, breakeven_offset=1,
                             trail_trigger=10, trail_offset=5)
    assert t.update(103) == 95      # profit 3 < 5, untouched
    assert t.update(105) == 101     # profit 5 -> breakeven+1
    assert t.update(108) == 101     # profit 8, still in breakeven band
    assert t.update(110) == 105     # profit 10 -> trail to entry+5
    assert t.update(107) == 105     # never loosens even if profit drops back


def test_trailing_stop_hit_long():
    t = TrailingStopManager(entry_price=100, initial_stop=95, side=Side.LONG)
    t.update(105)  # stop now 101
    assert t.hit(100) is True
    assert t.hit(102) is False


def test_trailing_stop_short_mirrors_long():
    t = TrailingStopManager(entry_price=100, initial_stop=105, side=Side.SHORT,
                             breakeven_trigger=5, breakeven_offset=1,
                             trail_trigger=10, trail_offset=5)
    assert t.update(97) == 105      # profit 3 < 5
    assert t.update(95) == 99       # profit 5 -> breakeven+1 below entry
    assert t.update(90) == 95       # profit 10 -> trail to entry-5
    assert t.hit(96) is True
    assert t.hit(94) is False
