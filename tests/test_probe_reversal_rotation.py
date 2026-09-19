import numpy as np
import pandas as pd

import probe_reversal_rotation as rr


def _matrices():
    # 3 stocks, 12 days, month-ends at rows 2, 6, 10. Stock A is the lowest-score pick.
    idx = pd.bdate_range("2024-01-01", periods=12)
    c = pd.DataFrame({"A": 100.0, "B": 100.0, "C": 100.0}, index=idx)
    c.iloc[3:, 0] = 120.0  # A jumps the day AFTER the first month-end ranking (row 2 -> 3)
    c.iloc[7:, 0] = 120.0
    return dict(close=c, high=c, low=c, me=[2, 6, 10])


def test_same_bar_fill_captures_a_jump_a_lagged_fill_cannot():
    M = _matrices()
    S = pd.DataFrame({"A": 0.0, "B": 1.0, "C": 2.0}, index=M["close"].index)  # A always the pick
    lag0 = rr.simulate(M, S, top_k=1, lag=0)
    lag1 = rr.simulate(M, S, top_k=1, lag=1)
    # month 1 (rows 2->6): lag0 buys at 100 and sells at 120 (+20%); lag1 buys at 120 after the jump (~0%)
    assert lag0["months"][0] > 0.19
    assert abs(lag1["months"][0]) < 0.01


def test_random_control_is_reproducible_and_ignores_scores():
    M = _matrices()
    S = pd.DataFrame({"A": 0.0, "B": 1.0, "C": 2.0}, index=M["close"].index)
    a = rr.simulate(M, S, 1, 1, rng=np.random.default_rng(5))["final"]
    b = rr.simulate(M, S, 1, 1, rng=np.random.default_rng(5))["final"]
    assert a == b
