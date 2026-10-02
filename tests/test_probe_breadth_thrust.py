import numpy as np
import pandas as pd

import probe_breadth_thrust as bt


def test_breadth_ratio_excludes_unchanged_days_and_counts_advances_declines():
    idx = pd.bdate_range("2024-01-01", periods=3)
    # day1->day2: A up, B down, C unchanged; day2->day3: A up, B up, C down
    close = pd.DataFrame({
        "A": [100.0, 110.0, 121.0],
        "B": [100.0, 90.0, 99.0],
        "C": [100.0, 100.0, 90.0],
    }, index=idx)
    b = bt.breadth_ratio({"close": close})
    assert np.isnan(b.iloc[0])
    assert abs(b.iloc[1] - 0.5) < 1e-12    # 1 advance, 1 decline, C unchanged excluded from denominator
    assert abs(b.iloc[2] - 2 / 3) < 1e-12  # A,B advance, C declines


def test_zweig_trend_matches_manual_ema_alpha_point_one():
    s = pd.Series([0.5, 0.6, 0.3])
    t = bt.zweig_trend(s, alpha=0.10)
    assert abs(t.iloc[0] - 0.5) < 1e-12
    assert abs(t.iloc[1] - (0.5 + 0.10 * (0.6 - 0.5))) < 1e-12
    assert abs(t.iloc[2] - (t.iloc[1] + 0.10 * (0.3 - t.iloc[1]))) < 1e-12


def test_thrust_fires_exactly_within_the_window_not_one_day_later():
    idx = pd.bdate_range("2024-01-01", periods=30)
    # trend sits at 0.40 on day 5, ramps linearly to 0.615 by day 15 (10 trading days later) -> fires
    vals = np.full(30, 0.50)
    vals[5] = 0.40
    for i in range(5, 16):
        vals[i] = 0.40 + (0.615 - 0.40) * (i - 5) / 10
    trend = pd.Series(vals, index=idx)
    ev = bt.thrust_events(trend, lookback=10, gap=1)
    assert 15 in ev  # day 15 is exactly 10 trading days after the day-5 low, >= high

    # same shape but stretched to 11 trading days -> must NOT fire under the literal 10-day rule
    vals2 = np.full(30, 0.50)
    vals2[5] = 0.40
    for i in range(5, 17):
        vals2[i] = 0.40 + (0.615 - 0.40) * (i - 5) / 11
    trend2 = pd.Series(vals2, index=idx)
    ev2 = bt.thrust_events(trend2, lookback=10, gap=1)
    assert 16 not in ev2


def test_thrust_events_are_declustered_to_one_per_episode():
    idx = pd.bdate_range("2024-01-01", periods=20)
    vals = np.full(20, 0.70)  # stays high the whole time, but a low touches day 0
    vals[0] = 0.30
    trend = pd.Series(vals, index=idx)
    ev = bt.thrust_events(trend, lookback=10, gap=5)
    assert len(ev) == 1  # every day from 1..9 individually qualifies; declustering keeps only the first


def test_thrust_events_ignore_nan_warmup_without_crashing():
    idx = pd.bdate_range("2024-01-01", periods=15)
    vals = np.concatenate([[np.nan] * 5, np.linspace(0.40, 0.70, 10)])
    trend = pd.Series(vals, index=idx)
    ev = bt.thrust_events(trend, lookback=10, gap=1)
    assert isinstance(ev, np.ndarray)  # no RuntimeWarning / crash on an all-NaN lookback window
