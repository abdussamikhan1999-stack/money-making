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
    return c / c.shift(window) - 1  # reversal: trailing return, lowest = biggest loser


def simulate(M, S, top_k, lag, rng=None, capital=100_000.0):
    """Long the top_k lowest-score names (random eligible names if rng given)."""
    c = M["close"].to_numpy()
    sc = S.to_numpy()
    me = M["me"]
    cap, peak, dd = capital, capital, 0.0
    months, at = [], []
    for a, b in zip(me[:-1], me[1:]):
        ea, eb = a + lag, b + lag
        if eb >= len(c):
            break
        ok = ~np.isnan(sc[a]) & ~np.isnan(c[ea]) & ~np.isnan(c[eb])
        cand = np.flatnonzero(ok)
        if len(cand) == 0:
            continue
        k = min(top_k, len(cand))
        pick = rng.choice(cand, size=k, replace=False) if rng is not None else cand[np.argsort(sc[a][cand])[:k]]
        each = cap / k
        pnl = 0.0
        for j in pick:
            r = c[eb, j] / c[ea, j] - 1
            pnl += each * (r - 2 * COST_PCT / 100) - DP
        cap += pnl
        peak = max(peak, cap)
        dd = max(dd, (peak - cap) / peak)
        months.append(pnl / (cap - pnl))
        at.append(a)
    return dict(final=cap, months=np.array(months), max_dd=dd, at=at)


def cagr(final, years, capital=100_000.0):
    return ((final / capital) ** (1 / years) - 1) * 100 if final > 0 else -100.0


def report(M, kind, window, top_k, lag, seeds, years):
    S = scores(M, kind, window)
    r = simulate(M, S, top_k, lag)
    n = len(r["months"])
    half = n // 2
    h1, h2 = np.prod(1 + r["months"][:half]) - 1, np.prod(1 + r["months"][half:]) - 1
    qs = [np.prod(1 + q) - 1 for q in np.array_split(r["months"], 4)]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1500)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--stress", action="store_true", help="add 4 real blowups (survivorship stress)")
    ap.add_argument("--overlap", action="store_true", help="how much is rev(21) just IBS?")
    ap.add_argument("--extend", action="store_true", help="EXPLORATORY: windows/top_k beyond the pre-registered grid")
    a = ap.parse_args()
    M = load_matrices(stress=a.stress)
    years = (M["close"].index[M["me"][-1]] - M["close"].index[M["me"][0]]).days / 365.25
    print(f"universe {M['close'].shape[1]} stocks, {len(M['me'])} month-ends, {years:.1f}y")
    if a.validate:
        print("VALIDATION: lag=0 IBS(5) should be ~22%/yr (family's Thirty-ninth-entry number)")
        print(report(M, "ibs", 5, 5, 0, 0, years)[0])
        return
    if a.overlap:
        return overlap(M, years)
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
