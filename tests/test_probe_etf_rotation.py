import numpy as np
import pandas as pd

import probe_etf_rotation as er


def _px(n=12):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"N": 100.0, "G": 100.0, "C": 100.0}, index=idx)


def test_decision_fills_next_close_so_a_move_into_that_close_is_not_earned():
    px = _px()
    px.iloc[1:, 0] = 130.0  # N jumps between the month-end decision row 0 and the fill row 1
    r = er.run(px, np.tile([1.0, 0.0, 0.0], (2, 1)), me=np.array([0, 5, 10]))
    assert abs(r[0] - (0.0 - 2 * er.COST)) < 1e-9  # jump excluded; only the switch cost (sell cash fund + buy N = 2 legs) is paid


def test_only_changes_of_holding_pay_cost():
    px = _px()
    r = er.run(px, np.tile([1.0, 0.0, 0.0], (2, 1)), me=np.array([0, 5, 10]))
    assert abs(r[0] + 2 * er.COST) < 1e-9 and abs(r[1]) < 1e-9  # first month pays the 2-leg switch, second holds unchanged
