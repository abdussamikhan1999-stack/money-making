import numpy as np
import pandas as pd

import probe_macro_analog as m


def test_pnl_from_signal_charges_one_leg_per_flip():
    # flips: 0->1, 1->0, 0->1 = 3 legs at 0.2% each
    assert abs(m.pnl_from_signal([1, 1, 0, 1], np.full(4, 0.1)) - (0.3 - 3 * 0.002)) < 1e-12


def test_subset_p_separates_real_from_random():
    rets = np.concatenate([np.full(5, 0.10), np.zeros(95)])
    assert m.subset_p(np.arange(100) < 5, rets, n_draws=5000) < 0.001
    assert m.subset_p(np.arange(100) >= 95, rets, n_draws=5000) > 0.5


def test_analog_forecast_ignores_candidates_whose_forward_window_is_still_open():
    rng = np.random.default_rng(0)
    n = 700
    f = pd.DataFrame(rng.normal(size=(n, len(m.FEATURES))), columns=m.FEATURES)
    f["fwd"] = 0.01
    t = n - 1
    f.loc[t - m.HOLD : t, "fwd"] = 1e6  # windows not yet closed at t: must be unreachable
    f.loc[t - m.HOLD : t - 1, m.FEATURES] = f.loc[t, m.FEATURES].to_numpy()  # ...and nearest by distance
    forecast, base = m.analog_forecast(f, t, k=5)
    assert abs(forecast - 0.01) < 1e-9 and abs(base - 0.01) < 1e-9  # a leaked 1e6 row would be off by ~1e5


def test_build_lags_non_indian_series_one_day():
    idx = pd.bdate_range("2020-01-01", periods=120)
    df = pd.DataFrame({"nifty": np.linspace(100, 200, 120), "ivix": 15.0, "vix": 15.0, "brent": np.linspace(50, 80, 120),
                       "inr": 70.0, "dxy": 90.0, "us10y": 2.0, "spx": 3000.0, "gold": 1500.0}, index=idx)
    a = m.build(df)["brent60"].iloc[-1]
    df.loc[idx[-1], "brent"] = 1e6  # a same-day (unavailable at India close) shock must not leak
    assert m.build(df)["brent60"].iloc[-1] == a
