import numpy as np
import pandas as pd

import probe_delivery_signal as ds
import probe_dividend_signal as dv
import probe_seasonality_signal as ss


def panel(months=180, names=40, seed=0, plant=0.0, start="2005-01-01"):
    """Business-day price panel whose month-i return = plant * (stock's own fixed value for that calendar month) + noise."""
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range(start, periods=months * 22)
    me = dv.month_end_positions(cal, today="2100-01-01")
    n_m = len(me)
    month_of = pd.DatetimeIndex(cal[me]).month.to_numpy()
    own = rng.normal(0, 0.03, size=(12, names))                      # stock x calendar-month fixed component
    ret = plant * own[month_of - 1] + rng.normal(0, 0.05, size=(n_m, names))
    P = np.ones((len(cal), names))
    lv = np.ones(names)
    prev = 0
    for i, pos in enumerate(me):                                      # geometric path, exact month-end returns
        for t in range(prev, pos + 1):
            P[t] = lv * (1 + ret[i]) ** ((t - prev + 1) / (pos - prev + 1)) if i else lv
        lv = lv * (1 + ret[i]) if i else lv
        prev = pos + 1
    return pd.DataFrame(P, index=cal, columns=[f"S{i}" for i in range(names)]), me


def test_seas1_is_the_return_of_the_same_calendar_month_a_year_earlier():
    A, me = panel(months=60)
    S = ss.seasonal_signals(A, me)["SEAS1"]
    M = ss.monthly_returns(A, me)
    d = 30                                                            # decision month d, target month d+1
    assert np.allclose(S.iloc[d].to_numpy(), M[d - 11])
    tgt, src = A.index[me][d + 1], A.index[me][d - 11]
    assert tgt.month == src.month and tgt.year - src.year == 1        # same calendar month, one year earlier


def test_seas3_and_seas5_average_annual_lags_and_need_full_history():
    A, me = panel(months=90)
    S, M = ss.seasonal_signals(A, me), ss.monthly_returns(A, me)
    d = 60
    assert np.allclose(S["SEAS3"].iloc[d], np.mean([M[d - 11], M[d - 23], M[d - 35]], axis=0))
    assert np.allclose(S["SEAS5"].iloc[d], np.mean([M[d - 11 - 12 * j] for j in range(5)], axis=0))
    assert S["SEAS5"].iloc[:48].isna().all().all()                    # 5 years of same-month history not there yet
    assert S["SEAS1"].iloc[12:].notna().all().all()


def test_a_missing_component_makes_the_name_nan_not_zero():
    A, me = panel(months=60, names=5)
    A.iloc[me[20], 0] = np.nan                                        # S0 has no price at one month-end
    S = ss.seasonal_signals(A, me)["SEAS3"]
    # the missing price at me[20] kills M_20 and M_21; SEAS3 at d reads M_{d-11}, M_{d-23}, M_{d-35} (valid from d=36)
    assert np.isnan(S.iloc[43, 0]) and np.isnan(S.iloc[44, 0])        # d-23 = 20, 21
    assert np.isnan(S.iloc[55, 0]) and np.isnan(S.iloc[56, 0])        # d-35 = 20, 21
    assert np.isfinite(S.iloc[45, 0]) and np.isfinite(S.iloc[43, 1])  # untouched month / untouched stock
    assert np.isnan(ss.seasonal_signals(A, me)["SEAS1"].iloc[31, 0])  # d-11 = 20


def test_signal_at_d_uses_no_price_after_month_end_d():
    A, me = panel(months=80, names=10)
    B = A.copy()
    B.iloc[me[50] + 1:] *= 3.7                                        # rewrite everything after ME_50
    a, b = ss.seasonal_signals(A, me), ss.seasonal_signals(B, me)
    for k in a:
        pd.testing.assert_frame_equal(a[k].iloc[:51], b[k].iloc[:51])


def test_planted_annual_seasonality_is_detected_and_a_null_panel_is_not():
    rng = np.random.default_rng(3)
    for plant, should in ((1.0, True), (0.0, False)):
        A, me = panel(months=170, names=50, seed=7, plant=plant)
        S = ss.seasonal_signals(A, me)["SEAS3"]
        F = dv.forward_monthly(A, me, 1)
        ic, p, n, series = ds.ic_test(S, F, rng, draws=1500, min_shift=12, min_rows=60, avoid_mod=12)
        t, p_nw = ds.nw_p(series, 1)
        assert n >= 60
        if should:
            assert ic > 0.2 and p < 0.03 and p_nw < 1e-6              # shift p cannot go below ~1/(distinct shifts) ~ 0.01
        else:
            assert abs(ic) < 0.08 and p_nw > 0.05
