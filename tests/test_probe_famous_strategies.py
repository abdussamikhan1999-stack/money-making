import numpy as np
import pandas as pd

import probe_famous_strategies as fs
import probe_vol_breakout as vb


def test_run_weights_unified_formula_pays_cash_on_the_unallocated_part_and_turnover_cost():
    W, R, C = np.array([[1.0], [0.0]]), np.array([[0.03], [0.02]]), np.array([0.01, 0.01])
    r = fs.run_weights(W, R, C)
    assert abs(r[0] - (0.03 - fs.COST * 1.0)) < 1e-12          # fully invested; entry turnover 1.0
    assert abs(r[1] - (0.01 - fs.COST * 1.0 * (1.03 / 1.0299))) < 1e-4  # in cash: earns cash, pays the exit turnover


def test_rotation_p_is_exact_and_perfect_foresight_hits_the_floor():
    rng = np.random.default_rng(0)
    n = 120
    R = rng.normal(0.005, 0.04, (n, 1))
    C = np.full(n, 0.002)
    W = (R > C[:, None]).astype(float)  # perfect foresight
    pS, pF = fs.rotation_p(W, R, C, min_shift=12)
    m = n - 12 - 12 + 1
    assert abs(pS - 1 / (m + 1)) < 1e-12 and abs(pF - 1 / (m + 1)) < 1e-12


def test_vol_breakout_trigger_uses_prior_range_and_fills_at_trigger_not_open():
    d = pd.DataFrame({"Open": [100.0, 100.0], "High": [110.0, 106.0], "Low": [100.0, 99.0], "Close": [105.0, 104.0]},
                     index=pd.bdate_range("2024-01-01", periods=2))
    r = vb.trades(d, 0.5, "long")  # day 1: prior range 10 -> trigger 105, high 106 hits, close 104
    assert len(r) == 1 and abs(r.iloc[0] - (104 / 105 - 1 - 2 * vb.COST)) < 1e-12
