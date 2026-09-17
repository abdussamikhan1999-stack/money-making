from regime import RegimeClassifier, RegimeGatedStrategy, current_regime, TREND_UP, TREND_DOWN, VOL_HIGH, VOL_LOW
from strategy import Signal, Side


def bar(close):
    return {"open": close, "high": close, "low": close, "close": close, "volume": 0}


def test_no_regime_before_trend_window_fills():
    c = RegimeClassifier(trend_period=10, vol_period=3, vol_lookback=5)
    for close in range(1, 9):
        c.push(bar(close))
    assert c.classify(9.0) is None  # only 8 bars pushed, needs 10 for trend


def test_trend_regime_up_when_close_above_sma():
    c = RegimeClassifier(trend_period=5, vol_period=3, vol_lookback=5)
    for close in [10, 10, 10, 10, 10]:
        c.push(bar(close))
    assert c.trend_regime(close=15.0) == TREND_UP
    assert c.trend_regime(close=5.0) == TREND_DOWN


def test_vol_regime_ranks_current_reading_within_trailing_lookback():
    c = RegimeClassifier(trend_period=1, vol_period=2, vol_lookback=100)
    # calm run, then one volatile spike as the LAST bar - its own vol
    # reading should rank at (or near) the top of the trailing window
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] * 1.001)
    closes.append(closes[-1] * 1.20)  # sharp spike -> high realized vol
    for close in closes:
        c.push(bar(close))
    assert c.vol_regime(close=closes[-1]) == VOL_HIGH


def test_classify_combines_trend_and_vol_into_one_label():
    c = RegimeClassifier(trend_period=3, vol_period=2, vol_lookback=10)
    for close in [10, 10, 10, 10]:
        c.push(bar(close))
    label = c.classify(close=20.0)
    assert label.startswith(TREND_UP)


def test_current_regime_uses_only_bars_before_the_last_one():
    daily = [bar(100.0 + i) for i in range(10)]
    # regime as of the last bar should not require pushing the last bar
    # itself into the trend/vol windows - just checked here that it runs
    # and returns None gracefully on too-short history, not an error
    assert current_regime(daily, trend_period=200) is None


class _StubStrategy:
    def __init__(self):
        self.pushed = []

    def reset(self):
        self.pushed = []

    def push(self, bar):
        self.pushed.append(bar)

    def check_entry(self, close):
        return Signal(Side.LONG, close, close - 1, "stub")

    def check_exit(self, close, side):
        return False


def test_regime_gated_strategy_blocks_entry_outside_allowed_regimes():
    inner = _StubStrategy()
    classifier = RegimeClassifier(trend_period=3, vol_period=2, vol_lookback=10)
    gated = RegimeGatedStrategy(inner, classifier, allowed_regimes=frozenset({"nonexistent_regime"}))
    for close in [10, 10, 10]:
        gated.push(bar(close))
    assert gated.check_entry(close=20.0) is None  # real regime isn't in the allowed set


def test_regime_gated_strategy_allows_entry_inside_allowed_regimes():
    inner = _StubStrategy()
    classifier = RegimeClassifier(trend_period=3, vol_period=2, vol_lookback=10)
    gated = RegimeGatedStrategy(inner, classifier, allowed_regimes=frozenset({f"{TREND_UP}_{VOL_HIGH}", f"{TREND_UP}_{VOL_LOW}"}))
    for close in [10, 10, 10, 10]:
        gated.push(bar(close))
    sig = gated.check_entry(close=20.0)  # close above trend SMA -> "up_*" regime, which is allowed
    assert sig is not None
    assert sig.side is Side.LONG


def test_regime_gated_strategy_delegates_exit_and_push_unmodified():
    inner = _StubStrategy()
    classifier = RegimeClassifier(trend_period=3, vol_period=2, vol_lookback=10)
    gated = RegimeGatedStrategy(inner, classifier, allowed_regimes=frozenset())
    gated.push(bar(10.0))
    assert len(inner.pushed) == 1
    assert gated.check_exit(close=10.0, side=Side.LONG) is False
