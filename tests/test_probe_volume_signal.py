import numpy as np
import pandas as pd

import probe_volume_signal as v


def panel(T=300, N=30, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-01", periods=T)
    cols = [f"S{i}" for i in range(N)]
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (T, N)), axis=0), index=idx, columns=cols)
    vol = pd.DataFrame(rng.lognormal(12, 0.4, (T, N)), index=idx, columns=cols)
    return close, vol


def test_volume_signal_at_t_is_unaffected_by_later_data():
    close, vol = panel()
    a = v.signals(vol, close)
    vol2, close2 = vol.copy(), close.copy()
    vol2.iloc[200:] *= 50
    close2.iloc[200:] *= 3
    b = v.signals(vol2, close2)
    for k in a:
        pd.testing.assert_frame_equal(a[k].iloc[:200], b[k].iloc[:200])


def test_surge_is_positive_and_sign_flips_with_the_5d_return():
    close, vol = panel()
    vol.iloc[-5:, 0] *= 10                         # last 5 days: 10x volume on stock S0
    close.iloc[-6:, 0] = np.linspace(100, 90, 6)   # ... while it falls
    s = v.signals(vol, close)
    assert s["V1"].iloc[-1, 0] > 1.5 and s["V2"].iloc[-1, 0] < -1.5   # surge on a falling stock -> negative V2
