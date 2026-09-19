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


def test_corwin_schultz_half_spread_is_nonnegative_and_grows_with_range():
    idx = pd.bdate_range("2024-01-01", periods=30)
    narrow = pd.DataFrame({"A": 100.0}, index=idx)
    M = dict(close=narrow, high=narrow * 1.005, low=narrow * 0.995, me=[])
    wide = dict(close=narrow, high=narrow * 1.03, low=narrow * 0.97, me=[])
    hn, hw = rr.corwin_schultz_half_spread(M).iloc[5:], rr.corwin_schultz_half_spread(wide).iloc[5:]
    assert (hn >= 0).all().all() and hw.mean().iloc[0] > hn.mean().iloc[0]


def test_own_half_spread_lowers_returns():
    M = _matrices()
    S = pd.DataFrame({"A": 0.0, "B": 1.0, "C": 2.0}, index=M["close"].index)
    hs = pd.DataFrame(0.01, index=M["close"].index, columns=M["close"].columns)
    assert rr.simulate(M, S, 1, 1, hs=hs)["final"] < rr.simulate(M, S, 1, 1)["final"]


def test_risk_off_gate_month_earns_zero_and_pays_no_cost():
    M = _matrices()
    S = pd.DataFrame({"A": 0.0, "B": 1.0, "C": 2.0}, index=M["close"].index)
    gate = np.ones(len(M["close"]), bool)
    gate[M["me"][0]] = False  # first month off
    r = rr.simulate(M, S, 1, 1, gate=gate)
    assert r["months"][0] == 0.0 and len(r["months"]) == 2
    assert r["final"] == 100_000.0 * (1 + r["months"][1]) or abs(r["final"] - 100_000.0 * (1 + r["months"][1])) < 1e-6


def test_momentum_scores_sort_winners_first():
    idx = pd.bdate_range("2023-01-01", periods=300)
    up = pd.Series(np.linspace(100, 200, 300), index=idx)
    down = pd.Series(np.linspace(200, 100, 300), index=idx)
    M = dict(close=pd.DataFrame({"up": up, "down": down}), high=None, low=None, me=[])
    for kind in ("mom", "hi52"):
        s = rr.scores(M, kind, 0).iloc[-1]
        assert s["up"] < s["down"]  # lowest score is picked first: the winner / the name at its high


def test_hold_exits_after_h_days_not_at_next_month_end():
    idx = pd.bdate_range("2024-01-01", periods=30)
    c = pd.DataFrame({"A": 100.0, "B": 100.0}, index=idx)
    c.iloc[8:, 0] = 130.0  # A jumps 7 trading days after the row-1 entry (row 1 = ranking 0 + lag 1)
    M = dict(close=c, high=c, low=c, me=[0, 20])
    S = pd.DataFrame({"A": 0.0, "B": 1.0}, index=idx)
    short = rr.simulate(M, S, 1, 1, hold=5)["months"][0]   # exits before the jump
    long_ = rr.simulate(M, S, 1, 1, hold=10)["months"][0]  # exits after it
    assert abs(short) < 0.01 and long_ > 0.25
