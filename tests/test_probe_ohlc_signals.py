import numpy as np
import pandas as pd

import probe_ohlc_signals as o


def panel(T=300, N=30, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-01", periods=T)
    cols = [f"S{i}" for i in range(N)]
    cl = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (T, N)), axis=0), index=idx, columns=cols)
    op = cl.shift(1) * (1 + rng.normal(0, 0.004, (T, N)))
    return op.fillna(cl), cl


def test_signals_at_t_use_nothing_after_t():
    op, cl = panel()
    a = o.signals(op, cl)
    op2, cl2 = op.copy(), cl.copy()
    op2.iloc[200:] *= 2
    cl2.iloc[200:] *= 3
    b = o.signals(op2, cl2)
    for k in a:
        pd.testing.assert_frame_equal(a[k].iloc[:200], b[k].iloc[:200])


def test_signal_directions_match_the_pre_registration():
    op, cl = panel()
    cl.iloc[-1, 0] = cl.iloc[-2, 0] * 1.5           # S0: one huge up day at the very end
    op.iloc[-1, 0] = cl.iloc[-2, 0] * 1.5           # ... entirely as an overnight gap (open == close, intraday flat)
    cl.iloc[-1, 0] = op.iloc[-1, 0]
    s = o.signals(op, cl)
    assert s["MAX"].iloc[-1, 0] < s["MAX"].iloc[-1, 1:].min()      # a big recent max => LOWEST MAX score (avoid)
    assert s["G5"].iloc[-1, 0] < s["G5"].iloc[-1, 1:].min()        # a big recent gap up => lowest reversal score
    assert s["O20"].iloc[-1, 0] > s["O20"].iloc[-1, 1:].max()      # ... but the HIGHEST persistence score
