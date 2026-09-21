import numpy as np
import pandas as pd

import probe_delivery_signal as d

CSV = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
       "AAA, EQ, 18-Sep-2026, 1, 1, 1, 1, 1, 1, 1, 1000, 1, 1, 500, 50.00\n"
       "BBB, EQ, 18-Sep-2026, 1, 1, 1, 1, 1, 1, 1, 2000, 1, 1, -, -\n"
       "CCC, BE, 18-Sep-2026, 1, 1, 1, 1, 1, 1, 1, 3000, 1, 1, 100, 10.00\n"
       "ZZZ, EQ, 18-Sep-2026, 1, 1, 1, 1, 1, 1, 1, 4000, 1, 1, 100, 10.00\n")


def panel(T=400, N=40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-10-01", periods=T)
    cols = [f"S{i}" for i in range(N)]
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (T, N)), axis=0), index=idx, columns=cols)
    dp = pd.DataFrame(rng.normal(50, 10, (T, N)), index=idx, columns=cols)
    return dp, close


def test_parser_keeps_only_eq_series_in_the_universe_and_coerces_dashes():
    df = d.parse_bhav(CSV, {"AAA", "BBB", "CCC"})
    assert list(df.symbol) == ["AAA", "BBB"]                                   # BE series and ZZZ (not wanted) dropped
    assert df.deliv_per.iloc[0] == 50.0 and np.isnan(df.deliv_per.iloc[1])      # '-' -> NaN, not a crash


def test_forward_return_enters_at_the_next_close_and_never_uses_day_t():
    c = pd.DataFrame({"X": [10.0, 99.0, 20.0, 30.0, 45.0]})
    f = d.fwd(c, 2)
    assert abs(f["X"].iloc[0] - (30.0 / 99.0 - 1)) < 1e-12                        # close[t+1] -> close[t+1+h]; close[t]=10 unused
    assert np.isnan(f["X"].iloc[-3:]).all()


def test_signal_at_t_is_unaffected_by_any_later_data():
    dp, close = panel()
    a = d.signals(dp, close)
    dp2, close2 = dp.copy(), close.copy()
    dp2.iloc[300:] = 99.0
    close2.iloc[300:] *= 3
    b = d.signals(dp2, close2)
    for k in a:
        pd.testing.assert_frame_equal(a[k].iloc[:300], b[k].iloc[:300])          # rows before the edit are identical


def test_ic_test_detects_an_implanted_signal_and_not_noise():
    dp, close = panel(T=500, N=40, seed=1)
    F = d.fwd(close, 5)
    rng = np.random.default_rng(3)
    S_true = F.fillna(0) + rng.normal(0, F.std().mean() * 2, F.shape)            # noisy copy of the future
    S_noise = pd.DataFrame(rng.normal(size=F.shape), index=F.index, columns=F.columns)
    ic, p, n, _ = d.ic_test(S_true.where(F.notna()), F, np.random.default_rng(1), draws=500)
    assert ic > 0.1 and p < 0.01 and n > 300
    ic2, p2, _, _ = d.ic_test(S_noise, F, np.random.default_rng(1), draws=500)
    assert abs(ic2) < 0.05 and p2 > 0.05


def test_top_excess_is_positive_when_the_signal_is_the_future_return():
    dp, close = panel(T=300, N=30, seed=2)
    F = d.fwd(close, 5)
    assert d.top_excess(F, F) > 0.01


def test_residualize_removes_the_control_and_keeps_independent_content():
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2020-01-01", periods=200)
    cols = [f"S{i}" for i in range(40)]
    X = pd.DataFrame(rng.normal(size=(200, 40)), index=idx, columns=cols)
    Z = pd.DataFrame(rng.normal(size=(200, 40)), index=idx, columns=cols)
    Y = X + 0.7 * Z                                                       # Y = control + independent part
    R = d.residualize(Y, [X])
    exact = R.corrwith(X.rank(axis=1), axis=1).abs().max()                # OLS residual is exactly orthogonal to the ranks
    rc_x = R.rank(axis=1).corrwith(X.rank(axis=1), axis=1).mean()         # (re-ranking the residual adds a little back)
    rc_z = R.rank(axis=1).corrwith(Z.rank(axis=1), axis=1).mean()
    assert exact < 1e-9 and abs(rc_x) < 0.1 and rc_z > 0.5
