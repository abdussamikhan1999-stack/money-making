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


def test_nw_p_respects_autocorrelation_and_detects_a_real_mean():
    rng = np.random.default_rng(2)
    n = 400
    e = rng.normal(size=n)
    ar = np.zeros(n)
    for i in range(1, n):
        ar[i] = 0.9 * ar[i - 1] + e[i]                       # strongly persistent, true mean 0
    t_naive = ar.mean() / (ar.std() / np.sqrt(n))
    t_nw, p_nw = d.nw_p(pd.Series(ar), lag=20)
    assert abs(t_nw) < abs(t_naive) and p_nw > 0.01          # HAC widens the SE for persistence (naive t is overconfident)
    t2, p2 = d.nw_p(pd.Series(rng.normal(0.3, 1.0, n)), lag=5)
    assert t2 > 4 and p2 < 1e-4                              # a real mean is detected
    assert np.isnan(d.nw_p(pd.Series([0.1] * 10), lag=2)[1])  # too short -> NaN, not a spurious p


def test_ic_test_reports_the_null_centre_for_a_persistent_signal():
    rng = np.random.default_rng(6)
    T, N = 300, 40
    idx = pd.bdate_range("2019-01-01", periods=T)
    cols = [f"S{i}" for i in range(N)]
    static = rng.normal(size=N)
    S = pd.DataFrame(np.tile(static, (T, 1)), index=idx, columns=cols)           # a static ranking
    F = pd.DataFrame(rng.normal(size=(T, N)) + 0.4 * static, index=idx, columns=cols)   # returns tilted toward it
    ic, p, n, ser = d.ic_test(S, F, np.random.default_rng(1), draws=300)
    assert ser.attrs["null_z0"] > 3                          # shifting a static ranking cannot move the null off its tilt


def test_apply_rule_requires_every_cell_both_tests_both_halves_and_the_cost_gate():
    def cell(u, per, ic=0.02, p=0.01, pnw=0.02, h1=0.01, h2=0.01, ex=0.004):
        return dict(universe=u, period=per, signal="S", h=5, ic=ic, p=p, p_nw=pnw, ic_h1=h1, ic_h2=h2, top5_excess=ex)
    good = [cell("A", "P1"), cell("A", "P2"), cell("B", "P1"), cell("B", "P2")]
    assert d.apply_rule(pd.DataFrame(good)).advance.iloc[0]
    for bad in (cell("B", "P2", ic=-0.01), cell("B", "P2", p=0.2), cell("B", "P2", pnw=0.2),   # sign / shift p / NW p
                cell("B", "P2", h2=-0.001), cell("B", "P2", ex=0.001)):                           # a negative half / below cost
        res = d.apply_rule(pd.DataFrame(good[:3] + [bad]))
        assert not res.advance.iloc[0] and res.cells.iloc[0] == 4


def test_write_atomic_never_leaves_a_truncated_target(tmp_path):
    target = str(tmp_path / "c.csv")
    d.write_atomic(pd.DataFrame({"a": [1, 2]}), target)
    d.write_atomic(pd.DataFrame({"a": [3, 4, 5]}), target)
    assert pd.read_csv(target).a.tolist() == [3, 4, 5] and not (tmp_path / "c.csv.tmp").exists()
