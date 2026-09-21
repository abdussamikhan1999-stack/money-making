import io
import zipfile

import numpy as np
import pandas as pd

import probe_regime_longhist as r


def test_decluster_keeps_first_then_only_rows_a_gap_later():
    f = np.zeros(40, bool)
    f[[0, 5, 12, 20, 30]] = True
    assert list(r.decluster(f)) == [0, 12, 30]


def test_regime_needs_both_a_30pct_jump_and_a_12m_high():
    w = np.r_[np.full(12, 50.0), 50, 50, 50, 70.0, 70, 70, 71]   # +40% in 3m at t=15 (a 12m high)
    fl = r.regime_flags(w, np.full(len(w), 4.0))
    assert fl["A"][15] and not fl["A"][14] and not fl["B"].any()   # flat yields -> no HIKING regime
    w2 = np.r_[np.full(12, 80.0), 50, 50, 50, 70.0]                # +40% off a low but far below the 12m high
    assert not r.regime_flags(w2, np.full(len(w2), 4.0))["A"].any()
    g = np.r_[np.full(15, 3.0), 3.8, 3.8, 3.8]                     # yields +0.8pp in 6m at the shock month
    assert r.regime_flags(w, np.r_[g, [3.8]])["B"][15]


def test_forward_uses_only_months_after_t():
    x = np.array([0.5, 0.1, 0.2, 0.3, 0.4])
    f = r.forward(x, 2)
    assert abs(f[0] - (1.1 * 1.2 - 1)) < 1e-12                     # months 1,2 - never month 0 itself
    assert np.isnan(f[-1]) and np.isnan(f[-2])                     # incomplete windows stay NaN


def test_random_spaced_respects_the_gap():
    c = r.random_spaced(np.arange(400), 5, 500, np.random.default_rng(0))
    assert c.shape == (500, 5) and (np.diff(c, axis=1) >= r.GAP).all()


def test_regime_test_finds_an_implanted_effect_and_not_noise():
    rng = np.random.default_rng(3)
    F = rng.normal(0, 0.05, 900)
    ev = np.array([100, 200, 300, 400, 500, 600])
    elig = np.ones(900, bool)
    F2 = F.copy()
    F2[ev] += 0.20
    assert r.regime_test(F2, ev, elig, np.random.default_rng(1), draws=2000)[4] < 0.01
    assert r.regime_test(F, ev, elig, np.random.default_rng(1), draws=2000)[4] > 0.05


def test_null_never_draws_months_before_the_inputs_existed():
    F = np.arange(1000, dtype=float)                               # value == row index
    elig = np.arange(1000) >= 700
    out = r.regime_test(F, np.array([750, 800, 850]), elig, np.random.default_rng(0), draws=500)
    assert out[2] >= 700 and out[1] == 800                         # baseline mean comes only from eligible rows


def test_trend_gate_reads_only_the_previous_month():
    mkt = np.array([0.01] * 12 + [-0.5, 0.2])
    out = r.trend_series(mkt, np.zeros(14))
    assert out[12] == -0.5                                         # month 12 was still risk-on at month 11
    assert out[13] == 0.0                                          # the crash put month 12 below its average
    assert np.isnan(out[:10]).all() and not np.isnan(out[10])       # no 10-month mean yet -> NaN, never risk-off


def test_bond_return_falls_when_yields_rise():
    b = r.bond_return(pd.Series([4.0, 5.0, 5.0]))
    assert b.iloc[1] < -0.05 and b.iloc[2] > 0


def test_french_parser_stops_at_the_annual_block(monkeypatch):
    txt = "junk\n\n,Mkt-RF,RF\n192607,  1.00, 0.10\n192608, -99.99, 0.20\n\n Annual\n,Mkt-RF,RF\n1927, 5, 3\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", txt)

    class R:
        def read(self):
            return buf.getvalue()

    monkeypatch.setattr(r.urllib.request, "urlopen", lambda *a, **k: R())
    df = r.french("x.zip")
    assert len(df) == 2 and abs(df.iloc[0]["Mkt-RF"] - 0.01) < 1e-12 and np.isnan(df.iloc[1]["Mkt-RF"])


def test_gold_forward_window_skips_the_flag_adjacent_month():
    x = np.array([0.0, 0.50, 0.10, 0.20, 0.30, 0.0])
    assert abs(r.fwd_for("MKT", x, 2)[0] - (1.5 * 1.1 - 1)) < 1e-12           # normal series: months 1,2
    assert abs(r.fwd_for("GOLD", x, 2)[0] - (1.1 * 1.2 - 1)) < 1e-12          # GOLD: months 2,3, month 1 skipped
    assert np.isnan(r.fwd_for("GOLD", x, 2)[-3:]).all()                        # windows shifted, ends stay incomplete
