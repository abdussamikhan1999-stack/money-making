"""
Fifty-ninth entry: (a) closes the Fifty-first entry's still-open backtest
look-ahead for IBS rotation (rank on the month-end close, fill at that SAME
close) by adding a fill LAG, and (b) tests the classic short-term reversal
factor (Jegadeesh 1990, Lehmann 1990: last week's/month's losers outperform)
under the same, lagged, machinery. Pre-registered grid: reversal window in
{5, 10, 21} trading days x top_k in {3, 5, 8}.

Self-contained numpy re-implementation of the monthly rotation family
(probe_ibs_rotation_widen.simulate_wide) so that fill lag can be applied to the
strategy AND its random-portfolio control identically. lag=0 must reproduce
the family's existing IBS numbers (validation of this file); lag=1 fills at
the first close after the month-end ranking date, the only fill a real account
could get after ranking on the close.

Same 52-stock WIDE_UNIVERSE, 10y, same cost model (0.2% STT+stamp each way,
Rs16 DP per pick), equal-weight monthly rebalance, compounding capital.
"""

import argparse
import pickle
import os

import numpy as np
import pandas as pd

from probe_ibs_rotation import fetch_calendar, month_end_dates
from probe_ibs_rotation_widen import build_wide_price_series

COST_PCT, DP = 0.2, 16.0
CACHE = os.environ.get("REV_CACHE", "")


def load_matrices(period="10y", stress=False):
    """stress=True: add the Fortieth entry's 4 real blowups (survivorship stress)."""
    cache = CACHE.replace(".pkl", "_stress.pkl") if (CACHE and stress) else CACHE
    if cache and os.path.exists(cache):
        return pickle.load(open(cache, "rb"))
    if stress:
        from probe_ibs_rotation_survivorship import build_stress_price_series
        series = build_stress_price_series(period)
    else:
        series = build_wide_price_series(period)
    cal = [c["date"].date() for c in fetch_calendar(period)]
    idx = pd.DatetimeIndex(cal)
    out = {}
    for name in ("close", "high", "low"):
        out[name] = pd.DataFrame(
            {s: pd.Series([c[name] for c in cds], index=pd.DatetimeIndex([c["date"].date() for c in cds]))
             for s, cds in series.items()}).reindex(idx).ffill()
    out["me"] = [idx.get_loc(pd.Timestamp(d)) for d in month_end_dates(fetch_calendar(period))]
    if cache:
        pickle.dump(out, open(cache, "wb"))
    return out


def scores(M, kind, window):
    c, h, l = M["close"], M["high"], M["low"]
    if kind == "ibs":
        ibs = ((c - l) / (h - l).replace(0, np.nan))
        return ibs.rolling(window, min_periods=window).mean()
    if kind == "hi52":  # George-Hwang: nearness to the 252d high; NEGATED so "lowest score" = nearest the high
        return -(c / c.rolling(252, min_periods=252).max())
    if kind == "mom":  # Jegadeesh-Titman 12-1: return from t-252 to t-21, NEGATED so highest momentum sorts first
        return -(c.shift(21) / c.shift(252) - 1)
    return c / c.shift(window) - 1  # reversal: trailing return, lowest = biggest loser


def corwin_schultz_half_spread(M, smooth=3):
    """Corwin & Schultz (2012) high-low bid-ask spread estimate, as a HALF spread
    (fraction of price) per stock-day: two-day window ending on that day, negatives
    floored at 0, smoothed over `smooth` days. Known upward bias on high-volatility
    days (range widened by volatility, not spread), which is exactly the day type
    IBS picks, so results are an UPPER-leaning estimate of that asymmetry."""
    h, l = M["high"], M["low"]
    k = 3 - 2 * np.sqrt(2)
    beta = np.log(h / l) ** 2 + np.log(h.shift(1) / l.shift(1)) ** 2
    gamma = np.log(np.maximum(h, h.shift(1)) / np.minimum(l, l.shift(1))) ** 2
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = (2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))).clip(lower=0)
    return (spread.rolling(smooth, min_periods=1).mean() / 2)


def simulate(M, S, top_k, lag, rng=None, capital=100_000.0, hs=None, hs_mult=1.0, gate=None):
    """Long the top_k lowest-score names (random eligible names if rng given).
    hs: optional half-spread matrix (see corwin_schultz_half_spread); each pick pays
    ITS OWN half spread x hs_mult on both legs, so the asymmetry between
    the strategy's picks and random picks is priced in, not assumed."""
    c = M["close"].to_numpy()
    sc = S.to_numpy()
    hsv = hs.to_numpy() if hs is not None else None
    me = M["me"]
    cap, peak, dd = capital, capital, 0.0
    months, at, spreads = [], [], []
    for a, b in zip(me[:-1], me[1:]):
        ea, eb = a + lag, b + lag
        if eb >= len(c):
            break
        if gate is not None and not gate[a]:  # risk-off month: sit in cash (0%, no cost)
            months.append(0.0)
            at.append(a)
            continue
        ok = ~np.isnan(sc[a]) & ~np.isnan(c[ea]) & ~np.isnan(c[eb])
        cand = np.flatnonzero(ok)
        if len(cand) == 0:
            continue
        k = min(top_k, len(cand))
        pick = rng.choice(cand, size=k, replace=False) if rng is not None else cand[np.argsort(sc[a][cand])[:k]]
        each = cap / k
        pnl = 0.0
        for j in pick:
            if hsv is not None:
                r = c[eb, j] * (1 - hs_mult * np.nan_to_num(hsv[eb, j])) / (c[ea, j] * (1 + hs_mult * np.nan_to_num(hsv[ea, j]))) - 1
            else:
                r = c[eb, j] / c[ea, j] - 1
            if hsv is not None:
                spreads.append(np.nan_to_num(hsv[ea, j]))
            pnl += each * (r - 2 * COST_PCT / 100) - DP
        cap += pnl
        peak = max(peak, cap)
        dd = max(dd, (peak - cap) / peak)
        months.append(pnl / (cap - pnl))
        at.append(a)
    return dict(final=cap, months=np.array(months), max_dd=dd, at=at, spreads=spreads)


def cagr(final, years, capital=100_000.0):
    return ((final / capital) ** (1 / years) - 1) * 100 if final > 0 else -100.0


def report(M, kind, window, top_k, lag, seeds, years):
    S = scores(M, kind, window)
    r = simulate(M, S, top_k, lag)
    n = len(r["months"])
    half = n // 2
    h1, h2 = np.prod(1 + r["months"][:half]) - 1, np.prod(1 + r["months"][half:]) - 1
    qs = [np.prod(1 + q) - 1 for q in np.array_split(r["months"], 4)]
    years = len(r["months"]) / 12  # annualize over the months actually invested (momentum needs ~1y of history first)
    line = (f"{kind}({window}) top_k={top_k} lag={lag}: {cagr(r['final'], years):6.2f}%/yr, maxDD {r['max_dd']:.1%}, "
            f"halves {h1:+.0%}/{h2:+.0%}, quarters {[f'{q:+.0%}' for q in qs]}")
    if seeds:
        rng = np.random.default_rng(0)
        finals = np.array([simulate(M, S, top_k, lag, rng=rng)["final"] for _ in range(seeds)])
        p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
        line += f", random mean {cagr(finals.mean(), years):5.2f}%/yr, p={p:.4f}"
        return line, p
    return line, None


def overlap(M, years, top_k=8):
    """Monthly-return correlation and pick overlap of rev(21) vs IBS(5), lag 1, plus
    the equal-weight blend of the two return streams."""
    S1, S2 = scores(M, "rev", 21), scores(M, "ibs", 5)
    r1, r2 = simulate(M, S1, top_k, 1), simulate(M, S2, top_k, 1)
    common = sorted(set(r1["at"]) & set(r2["at"]))  # rev(21) skips the months its window is still filling
    a = np.array([r1["months"][r1["at"].index(i)] for i in common])
    b = np.array([r2["months"][r2["at"].index(i)] for i in common])
    print(f"monthly-return correlation rev(21) vs IBS(5), top_k={top_k}: {np.corrcoef(a, b)[0, 1]:.2f}")
    sc1, sc2, ov = S1.to_numpy(), S2.to_numpy(), []
    for i in M["me"][:-1]:
        ok = ~np.isnan(sc1[i]) & ~np.isnan(sc2[i])
        c = np.flatnonzero(ok)
        p1, p2 = set(c[np.argsort(sc1[i][c])[:top_k]]), set(c[np.argsort(sc2[i][c])[:top_k]])
        ov.append(len(p1 & p2) / top_k)
    print(f"mean fraction of identical picks: {np.mean(ov):.0%}")
    blend = (a + b) / 2
    fin = 100_000 * np.prod(1 + blend)
    print(f"50/50 blend of the two return streams: {cagr(fin, years):.2f}%/yr vs rev {cagr(r1['final'], years):.2f}% / IBS {cagr(r2['final'], years):.2f}%")


def spread_test(M, years, seeds, top_k=5):
    """Asymmetric-slippage test (Fifty-second entry's open item)."""
    hs = corwin_schultz_half_spread(M)
    uni = np.nanmean(hs.to_numpy()[M["me"][0] + 1 :], axis=None)
    print(f"universe mean estimated HALF spread (all stock-days): {uni:.3%}")
    for kind, window in (("ibs", 5), ("rev", 21)):
        S = scores(M, kind, window)
        base = simulate(M, S, top_k, 1)
        print(f"\n{kind}({window}) top_k={top_k} lag=1, no spread cost: {cagr(base['final'], years):.2f}%/yr")
        rng = np.random.default_rng(0)
        rspreads = np.mean([np.mean(simulate(M, S, top_k, 1, rng=rng, hs=hs)["spreads"]) for _ in range(200)])
        with_hs = simulate(M, S, top_k, 1, hs=hs)
        print(f"  mean half-spread paid on entry: strategy picks {np.mean(with_hs['spreads']):.3%} vs random picks {rspreads:.3%} "
              f"(ratio {np.mean(with_hs['spreads']) / rspreads:.2f}x)")
        for mult in (1.0, 2.0):
            r = simulate(M, S, top_k, 1, hs=hs, hs_mult=mult)
            rng = np.random.default_rng(0)
            finals = np.array([simulate(M, S, top_k, 1, rng=rng, hs=hs, hs_mult=mult)["final"] for _ in range(seeds)])
            p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
            print(f"  own-half-spread x{mult:.0f} both legs: {cagr(r['final'], years):.2f}%/yr, maxDD {r['max_dd']:.1%}; "
                  f"random control {cagr(finals.mean(), years):.2f}%/yr; p={p:.4f}")


def gate_test(M, years, seeds):
    """Does going to cash when NIFTY < its L-day SMA (at the ranking close, known before the
    lag-1 fill) cut IBS rotation's drawdown without giving back the edge? Pre-registered:
    L in {100,150,200}, top_k in {5,8}. Control: same NUMBER of risk-off months chosen at random."""
    import probe_macro_analog as pm
    nifty = pm.load()["nifty"].dropna()
    nifty = nifty.reindex(M["close"].index.union(nifty.index)).ffill().reindex(M["close"].index)
    S = scores(M, "ibs", 5)
    for top_k in (5, 8):
        base = simulate(M, S, top_k, 1)
        print(f"\ntop_k={top_k} ungated: {cagr(base['final'], years):.2f}%/yr, maxDD {base['max_dd']:.1%}")
        for L in (100, 150, 200):
            sma = nifty.rolling(L, min_periods=L).mean()
            gate = (nifty > sma).fillna(True).to_numpy()
            r = simulate(M, S, top_k, 1, gate=gate)
            off = [a for a in M["me"][:-1] if not gate[a]]
            rng = np.random.default_rng(0)
            # control: turn the same number of (random) months off
            finals, dds = [], []
            for _ in range(seeds):
                idx = set(rng.choice(M["me"][:-1], size=len(off), replace=False)) if off else set()
                g = np.ones(len(nifty), bool)
                for i in idx:
                    g[i] = False
                rr = simulate(M, S, top_k, 1, gate=g)
                finals.append(rr["final"])
                dds.append(rr["max_dd"])
            finals = np.array(finals)
            p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
            pdd = ((np.array(dds) <= r["max_dd"]).sum() + 1) / (seeds + 1)
            mo = r["months"]
            h = len(mo) // 2
            qs = [np.prod(1 + q) - 1 for q in np.array_split(mo, 4)]
            print(f"    halves {np.prod(1 + mo[:h]) - 1:+.0%}/{np.prod(1 + mo[h:]) - 1:+.0%}, quarters {[f'{q:+.0%}' for q in qs]}, "
                  f"worst single month {mo.min():+.1%} (ungated {base['months'].min():+.1%}), Calmar {cagr(r['final'], years) / (100 * r['max_dd']):.2f} "
                  f"(ungated {cagr(base['final'], years) / (100 * base['max_dd']):.2f})")
            print(f"  SMA{L}: {len(off)}/{len(M['me']) - 1} months in cash -> {cagr(r['final'], years):.2f}%/yr, "
                  f"maxDD {r['max_dd']:.1%}; random {len(off)} off-months: {cagr(finals.mean(), years):.2f}%/yr, "
                  f"DD {np.mean(dds):.1%}; p(return)={p:.3f}, p(drawdown as low)={pdd:.3f}")


def index_gate_replication(seeds=2000):
    """Out-of-sample check of the trend gate, independent of IBS: the same monthly rule
    (hold the index next month iff its close > L-day SMA at this month-end; fill next
    close, 0.2%/leg cost on each switch) on NIFTY 2008+ (includes 2008) and the S&P 500 1950+."""
    import yfinance as yf
    import probe_macro_analog as pm

    def spx():
        x = yf.Ticker("^GSPC").history(start="1950-01-01")["Close"]
        x.index = pd.DatetimeIndex(x.index.date)
        return x

    for name, px in (("NIFTY 2008+", pm.load()["nifty"].dropna()), ("S&P 500 1950+", spx())):
        px = px[~px.index.duplicated()]
        me = np.flatnonzero(px.index.to_period("M").to_series().ne(px.index.to_period("M").to_series().shift(-1)).to_numpy())
        p = px.to_numpy()
        years = (px.index[-1] - px.index[0]).days / 365.25
        rets = np.array([p[b + 1] / p[a + 1] - 1 for a, b in zip(me[:-1], me[1:]) if b + 1 < len(p)])
        starts = [a for a, b in zip(me[:-1], me[1:]) if b + 1 < len(p)]
        def stats(r):
            eq = np.cumprod(1 + r)
            return eq[-1], (1 - eq / np.maximum.accumulate(eq)).max()
        bh_f, bh_dd = stats(rets)
        yrs = len(rets) / 12
        cg = lambda f: (f ** (1 / yrs) - 1) * 100
        print(f"\n{name}: {len(rets)} months, buy&hold {cg(bh_f):.2f}%/yr, month-end maxDD {bh_dd:.1%}, Calmar {cg(bh_f) / (100 * bh_dd):.2f}")
        for L in (100, 150, 200):
            sma = pd.Series(p).rolling(L).mean().to_numpy()
            on = np.array([(p[a] > sma[a]) if not np.isnan(sma[a]) else True for a in starts])
            flips = np.abs(np.diff(np.concatenate([[1], on.astype(int)]))).sum()
            g = np.where(on, rets, 0.0) - np.where(np.abs(np.diff(np.concatenate([[1], on.astype(int)]))) > 0, 0.002, 0.0)
            gf, gdd = stats(g)
            rng = np.random.default_rng(0)
            n_off = int((~on).sum())
            fin, dds = [], []
            for _ in range(seeds):
                m = np.ones(len(rets), bool)
                m[rng.choice(len(rets), n_off, replace=False)] = False
                f_, d_ = stats(np.where(m, rets, 0.0))
                fin.append(f_)
                dds.append(d_)
            pdd = ((np.array(dds) <= gdd).sum() + 1) / (seeds + 1)
            pret = ((np.array(fin) >= gf).sum() + 1) / (seeds + 1)
            print(f"  SMA{L}: cash {n_off}/{len(rets)} months, {int(flips)} switches, {cg(gf):.2f}%/yr, maxDD {gdd:.1%}, "
                  f"Calmar {cg(gf) / (100 * gdd):.2f}; vs random off-months: p(return)={pret:.3f}, p(DD)={pdd:.4f}")


def freq_test(M, seeds):
    """Rebalance-frequency sweep for IBS(5), top_k=5, lag 1: every 5, 10 or 21 trading days
    (pre-registered). Same costs per rebalance, so higher frequency pays more turnover."""
    S = scores(M, "ibs", 5)
    n = len(M["close"])
    for step in (5, 10, 21):
        Mf = dict(M)
        Mf["me"] = list(range(M["me"][0], n - 2, step))
        r = simulate(Mf, S, 5, 1)
        yrs = len(r["months"]) * step / 252
        rng = np.random.default_rng(0)
        finals = np.array([simulate(Mf, S, 5, 1, rng=rng)["final"] for _ in range(seeds)])
        p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
        mo = r["months"]
        h = len(mo) // 2
        qs = [np.prod(1 + q) - 1 for q in np.array_split(mo, 4)]
        print(f"every {step:2d}d ({len(mo)} rebalances, {yrs:.1f}y): {cagr(r['final'], yrs):6.2f}%/yr, maxDD {r['max_dd']:.1%}, "
              f"halves {np.prod(1 + mo[:h]) - 1:+.0%}/{np.prod(1 + mo[h:]) - 1:+.0%}, quarters {[f'{q:+.0%}' for q in qs]}, "
              f"random mean {cagr(finals.mean(), yrs):.2f}%/yr, p={p:.4f}")


def phase_test(M, seeds, kind="ibs", window=5, top_k=5, step=21):
    """Is the monthly rotation's edge specific to CALENDAR month-end dates? Same signal, same 21-day
    step, every phase offset 0..20 from the first month-end, vs the calendar month-end grid itself."""
    S = scores(M, kind, window)
    n = len(M["close"])
    cal = simulate(M, S, top_k, 1)
    print(f"{kind}({window}) top_k={top_k}: calendar month-end grid {cagr(cal['final'], len(cal['months']) / 12):.2f}%/yr")
    res = []
    for off in range(step):
        Mf = dict(M)
        Mf["me"] = list(range(M["me"][0] + off, n - 2, step))
        r = simulate(Mf, S, top_k, 1)
        yrs = len(r["months"]) * step / 252
        rng = np.random.default_rng(0)
        finals = np.array([simulate(Mf, S, top_k, 1, rng=rng)["final"] for _ in range(seeds)])
        p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
        res.append((off, cagr(r["final"], yrs), cagr(finals.mean(), yrs), p))
    for off, c, rc, p in res:
        print(f"  offset {off:2d}d: {c:6.2f}%/yr vs random {rc:5.2f}%  p={p:.3f}")
    cs = np.array([r[1] for r in res])
    print(f"  across 21 phases: mean {cs.mean():.2f}%/yr, median {np.median(cs):.2f}, min {cs.min():.2f}, max {cs.max():.2f}; "
          f"phases with p<0.05: {sum(1 for r in res if r[3] < 0.05)}/21")


def anchor_test(M, seeds, kind="ibs", window=5, top_k=5):
    """Calendar-ANCHORED phase: rebalance on the j-th trading day BEFORE each month-end (j=0 is the
    calendar month-end used everywhere else; j~10 is mid-month). Unlike a fixed 21-day step this does
    not drift against the calendar, so it asks directly: is the edge a turn-of-month effect?"""
    S = scores(M, kind, window)
    print(f"{kind}({window}) top_k={top_k}, rebalance j trading days before each month-end (lag-1 fills):")
    rows = []
    for j in range(0, 21):
        Mj = dict(M)
        Mj["me"] = [m - j for m in M["me"] if m - j > 260 * 0 and m - j >= 0]
        r = simulate(Mj, S, top_k, 1)
        yrs = len(r["months"]) / 12
        rng = np.random.default_rng(0)
        finals = np.array([simulate(Mj, S, top_k, 1, rng=rng)["final"] for _ in range(seeds)])
        p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
        rows.append((j, cagr(r["final"], yrs), cagr(finals.mean(), yrs), p, r["max_dd"]))
        print(f"  j={j:2d}: {rows[-1][1]:6.2f}%/yr vs random {rows[-1][2]:5.2f}%, maxDD {rows[-1][4]:.1%}, p={p:.3f}")
    c = np.array([r[1] for r in rows])
    print(f"  across 21 anchors: mean {c.mean():.2f}%/yr, median {np.median(c):.2f}, min {c.min():.2f}, max {c.max():.2f}; "
          f"anchors with p<0.05: {sum(1 for r in rows if r[3] < 0.05)}/21; "
          f"last-5-days-of-month anchors (j=0..4) mean {c[:5].mean():.2f} vs rest {c[5:].mean():.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1500)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--stress", action="store_true", help="add 4 real blowups (survivorship stress)")
    ap.add_argument("--overlap", action="store_true", help="how much is rev(21) just IBS?")
    ap.add_argument("--spread", action="store_true", help="price each pick's own Corwin-Schultz half spread")
    ap.add_argument("--gate", action="store_true", help="NIFTY SMA trend gate on IBS rotation")
    ap.add_argument("--index-gate", action="store_true", help="trend gate on NIFTY 2008+ and S&P 1950+")
    ap.add_argument("--momentum", action="store_true", help="12-1 momentum and 52-week-high rotation")
    ap.add_argument("--freq", action="store_true", help="IBS rebalance frequency 5/10/21 days")
    ap.add_argument("--phase", action="store_true", help="IBS 21d-step phase offsets vs calendar month-end")
    ap.add_argument("--anchor", action="store_true", help="IBS/rev rebalance j days before month-end, j=0..20")
    ap.add_argument("--extend", action="store_true", help="EXPLORATORY: windows/top_k beyond the pre-registered grid")
    a = ap.parse_args()
    if a.index_gate:
        return index_gate_replication()
    M = load_matrices(stress=a.stress)
    years = (M["close"].index[M["me"][-1]] - M["close"].index[M["me"][0]]).days / 365.25
    print(f"universe {M['close'].shape[1]} stocks, {len(M['me'])} month-ends, {years:.1f}y")
    if a.validate:
        print("VALIDATION: lag=0 IBS(5) should be ~22%/yr (family's Thirty-ninth-entry number)")
        print(report(M, "ibs", 5, 5, 0, 0, years)[0])
        return
    if a.overlap:
        return overlap(M, years)
    if a.anchor:
        anchor_test(M, a.seeds)
        anchor_test(M, a.seeds, kind="rev", window=21, top_k=8)
        return
    if a.phase:
        phase_test(M, a.seeds)
        phase_test(M, a.seeds, kind="rev", window=21, top_k=8)
        return
    if a.freq:
        return freq_test(M, a.seeds)
    if a.momentum:
        print(f"=== basket-wide momentum rotations, lag 1, {a.seeds}-seed control (pre-registered 2 signals x top_k 3/5/8) ===")
        for kind in ("mom", "hi52"):
            for top_k in (3, 5, 8):
                print(report(M, kind, 0, top_k, 1, a.seeds, years)[0])
        return
    if a.spread:
        return spread_test(M, years, a.seeds)
    if a.gate:
        return gate_test(M, years, a.seeds)
    if a.index_gate:
        return index_gate_replication()
    if a.extend:
        print("=== EXPLORATORY extension past the pre-registered grid's edge (no p claimed as pre-registered) ===")
        for window, top_k in ((21, 12), (42, 8), (63, 8), (42, 12)):
            print(report(M, "rev", window, top_k, 1, a.seeds, years)[0])
        return
    print("\n=== IBS rotation: same-bar fill (lag 0, family convention) vs next-close fill (lag 1) ===")
    for top_k in (3, 5, 8):
        for lag in (0, 1):
            print(report(M, "ibs", 5, top_k, lag, a.seeds, years)[0])
    print("\n=== short-term reversal rotation, lag 1 (pre-registered 3x3 grid) ===")
    for window in (5, 10, 21):
        for top_k in (3, 5, 8):
            print(report(M, "rev", window, top_k, 1, a.seeds, years)[0])


if __name__ == "__main__":
    main()
