from indicators import (
    true_range, average_true_range, sma, rsi, stdev, highest, lowest, linreg,
    chaikin_money_flow, on_balance_volume, internal_bar_strength, ema_update,
)


def candle(h, l, c, v=0):
    return {"high": h, "low": l, "close": c, "volume": v}


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


def test_stdev_zero_for_constant_values():
    assert stdev([5, 5, 5, 5], period=4) == 0


def test_stdev_matches_population_formula():
    # [2, 4, 4, 4, 5, 5, 7, 9] has a well-known population stdev of 2.0
    values = [2, 4, 4, 4, 5, 5, 7, 9]
    assert abs(stdev(values, period=8) - 2.0) < 1e-9


def test_stdev_none_when_not_enough_history():
    assert stdev([1, 2], period=5) is None


def test_highest_and_lowest_over_window():
    values = [3, 7, 1, 9, 4]
    assert highest(values, period=3) == 9  # max of last 3: 1,9,4
    assert lowest(values, period=3) == 1


def test_highest_lowest_none_when_not_enough_history():
    assert highest([1, 2], period=5) is None
    assert lowest([1, 2], period=5) is None


def test_linreg_matches_last_value_on_a_perfect_line():
    # y = 2x + 1 for x = 0..4 -> fitted line passes through every point exactly,
    # so the fitted value at the last point equals the last actual value
    values = [1, 3, 5, 7, 9]
    assert abs(linreg(values) - 9) < 1e-9


def test_linreg_flat_series_returns_the_flat_value():
    assert abs(linreg([5, 5, 5, 5]) - 5) < 1e-9


def test_linreg_none_with_fewer_than_two_points():
    assert linreg([5]) is None


def test_chaikin_money_flow_all_closes_at_high_is_fully_positive():
    candles = [candle(110, 100, 110, v=100) for _ in range(3)]  # close at high every day -> mfm=+1
    assert abs(chaikin_money_flow(candles, period=3) - 1.0) < 1e-9


def test_chaikin_money_flow_all_closes_at_low_is_fully_negative():
    candles = [candle(110, 100, 100, v=100) for _ in range(3)]  # close at low every day -> mfm=-1
    assert abs(chaikin_money_flow(candles, period=3) - (-1.0)) < 1e-9


def test_chaikin_money_flow_none_when_not_enough_history():
    assert chaikin_money_flow([candle(110, 100, 105, v=100)], period=3) is None


def test_chaikin_money_flow_none_when_window_has_zero_volume():
    candles = [candle(110, 100, 105, v=0) for _ in range(3)]
    assert chaikin_money_flow(candles, period=3) is None


def test_on_balance_volume_nets_signed_volume_over_the_window():
    candles = [candle(0, 0, c, v=v) for c, v in [(10, 999), (12, 50), (11, 30), (13, 70)]]
    # day-over-day: +50 (12>10), -30 (11<12), +70 (13>11) -> net 90
    assert on_balance_volume(candles, period=3) == 90


def test_on_balance_volume_flat_day_contributes_nothing():
    candles = [candle(0, 0, c, v=v) for c, v in [(10, 999), (10, 50), (12, 30)]]
    # day1 flat (10==10) contributes 0, day2 up contributes +30
    assert on_balance_volume(candles, period=2) == 30


def test_on_balance_volume_none_when_not_enough_history():
    candles = [candle(0, 0, 10, v=100), candle(0, 0, 11, v=100)]
    assert on_balance_volume(candles, period=3) is None


def test_internal_bar_strength_at_the_high_is_one():
    assert internal_bar_strength(candle(110, 100, 110)) == 1.0


def test_internal_bar_strength_at_the_low_is_zero():
    assert internal_bar_strength(candle(110, 100, 100)) == 0.0


def test_internal_bar_strength_midpoint_is_half():
    assert internal_bar_strength(candle(110, 100, 105)) == 0.5


def test_internal_bar_strength_none_on_zero_range_bar():
    assert internal_bar_strength(candle(100, 100, 100)) is None


def test_ema_update_matches_the_standard_recursive_formula():
    # k = 2/(period+1) = 2/6 = 1/3 for period=5
    assert ema_update(prev_ema=100.0, price=130.0, period=5) == 110.0


def test_ema_update_returns_the_seed_unchanged_when_price_equals_it():
    assert ema_update(prev_ema=50.0, price=50.0, period=10) == 50.0
