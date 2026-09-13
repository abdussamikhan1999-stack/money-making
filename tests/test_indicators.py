from indicators import true_range, average_true_range


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
