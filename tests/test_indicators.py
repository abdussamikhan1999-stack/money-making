from indicators import true_range, average_true_range, sma, rsi


def candle(h, l, c):
    return {"high": h, "low": l, "close": c}


def test_true_range_picks_largest_of_three_measures():
    # high-low is largest
    assert true_range(prev_close=100, high=110, low=105) == 10
    # gap up: high vs prev_close dominates
    assert true_range(prev_close=100, high=120, low=115) == 20
    # gap down: prev_close vs low dominates
    assert true_range(prev_close=100, high=95, low=80) == 20


def test_average_true_range_none_when_not_enough_history():
    candles = [candle(105, 100, 102) for _ in range(5)]
    assert average_true_range(candles, period=14) is None


def test_average_true_range_computes_over_window():
    # flat 10-wide ranges, no gaps -> ATR should be exactly 10
    candles = [candle(110, 100, 105) for _ in range(15)]
    assert average_true_range(candles, period=14) == 10


def test_sma_averages_the_last_n_values():
    assert sma([1, 2, 3, 4, 5], period=3) == 4  # (3+4+5)/3
    assert sma([1, 2, 3]) == 2  # default: all values


def test_sma_none_when_not_enough_history():
    assert sma([1, 2], period=5) is None


def test_rsi_none_when_not_enough_history():
    assert rsi([1, 2, 3], period=2, seed_window=5) is None


def test_rsi_100_on_an_unbroken_uptrend():
    closes = [10, 11, 12, 13, 14, 15]  # every day up -> no losses at all
    assert rsi(closes, period=2, seed_window=5) == 100.0


def test_rsi_0_on_an_unbroken_downtrend():
    closes = [15, 14, 13, 12, 11, 10]  # every day down -> no gains at all
    assert rsi(closes, period=2, seed_window=5) == 0.0
