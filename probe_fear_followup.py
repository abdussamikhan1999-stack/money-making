"""
Fifty-eighth entry: follow-ups on the Fifty-seventh entry's VIX-spike fear-buy.

  1. Grid-free re-test: every DAY the trigger is on, declustered to one entry
     per 21 trading days (removes the monthly decision grid the Fifty-seventh
     entry's candidate A turned out to depend on).
  2. Drawdown rival: does India VIX add anything once NIFTY's drawdown from its
     252d high is controlled for? (OLS of forward 21d return on both, with
     Newey-West errors since daily windows overlap), plus a drawdown-only
     trigger matched on event count.
  3. IBS rotation overlap: how much of the long-only IBS rotation's P&L comes
     from the months right after a VIX spike (i.e. is the standing finding
     partly this trigger in disguise)?

Same timing as probe_macro_analog.py: US/global series lagged 1 day, forward
window starts the day AFTER the signal date.
"""

import argparse

import numpy as np
import pandas as pd

import probe_macro_analog as m

HOLD = m.HOLD


def declustered_events(mask, gap=HOLD):
    """One event per spike CLUSTER: an 'on' day with no 'on' day in the previous `gap` rows. The gap is measured
    from the last 'on' day, not the last accepted event (review finding: measuring from the last event re-entered
    every `gap` days inside a long spike, i.e. mid-spike entries counted as first crossings)."""
    idx, last_on = [], -10**9
    for i in np.flatnonzero(mask):
        if i - last_on >= gap:
            idx.append(i)
        last_on = i
    return np.array(idx, dtype=int)


def random_pool_p(fwd_all, event_fwd, n_draws=100_000, seed=1):
    """P(mean of n random days' forward return >= observed mean), (count+1)/(n+1)."""
    rng = np.random.default_rng(seed)
    n = len(event_fwd)
    obs = event_fwd.mean()
    hits = 0
    for _ in range(n_draws // 10_000):
        draw = rng.choice(fwd_all, size=(10_000, n))
        hits += int((draw.mean(axis=1) >= obs).sum())
    return (hits + 1) / (n_draws + 1)


def newey_west_ols(y, X, lags=HOLD):
    """OLS betas and Newey-West t-stats (Bartlett kernel)."""
    X = np.column_stack([np.ones(len(y)), X])
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    e = y - X @ beta
    Xe = X * e[:, None]
    S = Xe.T @ Xe
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        G = Xe[L:].T @ Xe[:-L]
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return beta, beta / np.sqrt(np.diag(V))


def grid_free(f):
    fwd = f["fwd"].to_numpy()
    valid = ~np.isnan(fwd) & (f.index >= "2010-01-01")
    pool = fwd[valid]
    print(f"unconditional (2010+): mean fwd21 {pool.mean():+.2%}, up-days {(pool > 0).mean():.0%}, n={len(pool)}")
    out = {}
    for col in ("ivix_rel", "vix_rel"):
        for x in (1.4, 1.5, 1.7):
            mask = (f[col].to_numpy() >= x) & valid
            ev = declustered_events(mask)
            evf = fwd[ev]
            p = random_pool_p(pool, evf)
            yrs = sorted({f.index[i].year for i in ev})
            out[(col, x)] = (len(ev), evf.mean(), p)
            print(f"{col}>={x}: {len(ev):2d} events (grid-free, >=21d apart), mean fwd21 {evf.mean():+.2%}, "
                  f"median {np.median(evf):+.2%}, positive {int((evf > 0).sum())}/{len(ev)}, min {evf.min():+.1%}, "
                  f"random-day p={p:.5f}, years {yrs}")
    return out


def drawdown_rival(f):
    fwd = f["fwd"].to_numpy()
    ok = ~np.isnan(fwd) & f[["ivix_rel", "nifty_dd"]].notna().all(axis=1).to_numpy() & (f.index >= "2010-01-01")
    y = fwd[ok]
    z = lambda s: (s - s.mean()) / s.std()
    ivr = f["ivix_rel"].to_numpy()[ok]
    dd = f["nifty_dd"].to_numpy()[ok]
    print("\n--- OLS fwd21 on standardised regressors, Newey-West(21) t-stats ---")
    for name, X in [("ivix_rel only", z(ivr)[:, None]), ("drawdown only (dd is <=0, so negative beta = buy dips)", z(dd)[:, None]),
                    ("both", np.column_stack([z(ivr), z(dd)]))]:
        b, t = newey_west_ols(y, X)
        print(f"{name:55s} betas {np.round(b[1:], 4)}  t {np.round(t[1:], 2)}")
    # count-matched drawdown-only trigger vs India VIX trigger
    valid = ~np.isnan(fwd) & (f.index >= "2010-01-01")
    pool = fwd[valid]
    ev_v = declustered_events((f["ivix_rel"].to_numpy() >= 1.5) & valid)
    n = len(ev_v)
    ddv = f["nifty_dd"].to_numpy()
    best = None
    for thr in np.arange(-0.03, -0.30, -0.005):
        ev = declustered_events((ddv <= thr) & valid)
        if len(ev) <= n:
            best = (thr, ev)
            break
    thr, ev_d = best
    both = len(set(ev_v) & set(ev_d))
    print(f"\ncount-matched: India-VIX>=1.5 {n} events mean {fwd[ev_v].mean():+.2%} (p={random_pool_p(pool, fwd[ev_v]):.5f}); "
          f"drawdown<={thr:.1%} {len(ev_d)} events mean {fwd[ev_d].mean():+.2%} (p={random_pool_p(pool, fwd[ev_d]):.5f}); "
          f"identical entry days: {both}")
    # inside deep-drawdown days, does VIX still separate outcomes?
    deep = valid & (ddv <= -0.08)
    hi = deep & (f["ivix_rel"].to_numpy() >= 1.3)
    lo = deep & (f["ivix_rel"].to_numpy() < 1.3)
    print(f"inside drawdown<=-8% days: VIX-rel>=1.3 n={int(hi.sum())} mean {fwd[hi].mean():+.2%}; <1.3 n={int(lo.sum())} mean {fwd[lo].mean():+.2%} "
          f"(overlapping daily windows, descriptive)")


def ibs_overlap(f, thr=1.4):
    from probe_ibs_rotation import fetch_calendar, month_end_dates, simulate
    from probe_ibs_rotation_widen import build_wide_price_series

    cal = fetch_calendar("10y")
    dates = month_end_dates(cal)
    res = simulate(dates, build_wide_price_series("10y"), top_k=5, lookback=5, capital=100_000.0)
    rets, rel = [], []
    capital = 100_000.0
    for mo in res["months"]:
        rets.append(mo["pnl"] / capital)
        capital += mo["pnl"]
        d = pd.Timestamp(mo["date"])
        i = f.index.searchsorted(d, side="right") - 1
        rel.append(f["ivix_rel"].iloc[i])
    rets, rel = np.array(rets), np.array(rel)
    finite = np.isfinite(rets) & np.isfinite(rel)  # yfinance occasionally returns a NaN bar; report, don't hide
    print(f"dropped {int((~finite).sum())} non-finite month(s) of {len(rets)}")
    keep = [(mo, r, l) for mo, r, l, ok in zip(res["months"], rets, rel, finite) if ok]
    res = dict(months=[k[0] for k in keep])
    rets, rel = np.array([k[1] for k in keep]), np.array([k[2] for k in keep])
    on = rel >= thr
    print(f"\n--- IBS rotation (52 stocks, top_k=5, monthly, {len(rets)} months) vs India-VIX spike at rebalance date (rel>={thr}) ---")
    print(f"months after a spike: {int(on.sum())}, mean IBS-rotation return {rets[on].mean():+.2%}; other months {rets[~on].mean():+.2%}")
    tot = np.prod(1 + rets) - 1
    ex = np.prod(1 + rets[~on]) - 1
    print(f"compounded total {tot:+.1%}; with spike-following months removed {ex:+.1%} ({int((~on).sum())} months)")
    rng = np.random.default_rng(3)
    obs = rets[on].mean()
    p = (sum(rets[rng.choice(len(rets), int(on.sum()), replace=False)].mean() >= obs for _ in range(50_000)) + 1) / 50_001
    print(f"random-month p for 'IBS rotation does better right after spikes': {p:.4f}")
    print("spike months:", [(str(mo['date']), f"{r:+.1%}") for mo, r, o in zip(res['months'], rets, on) if o])


def entry_timing(f, col="ivix_rel", x=1.5):
    """EXPLORATORY / post hoc (motivated by the grid-vs-grid-free gap, so no
    significance is claimed): does waiting k days after the first crossing, or
    entering in the persistent 'on' state, change the forward return?"""
    close = f["nifty"].to_numpy()
    valid = ~np.isnan(f["fwd"].to_numpy()) & (f.index >= "2010-01-01")
    on = (f[col].to_numpy() >= x) & valid
    first = declustered_events(on)
    print(f"\n--- entry timing after first crossing of {col}>={x} ({len(first)} events; post hoc, no p claimed) ---")
    for k in (0, 3, 5, 10, 15):
        r = np.array([close[i + k + 1 + HOLD] / close[i + k + 1] - 1 for i in first if i + k + 1 + HOLD < len(close)])
        print(f"enter first-crossing +{k:2d}d: mean {r.mean():+.2%}, median {np.median(r):+.2%}, positive {int((r > 0).sum())}/{len(r)}, min {r.min():+.1%}")
    fwd = f["fwd"].to_numpy()
    days_since = np.full(len(on), -1)
    run = -1
    for i in range(len(on)):
        run = run + 1 if on[i] else -1
        days_since[i] = run
    for lo, hi in ((0, 0), (1, 5), (6, 15), (16, 999)):
        mk = on & (days_since >= lo) & (days_since <= hi)
        print(f"state 'on' day #{lo}-{hi if hi < 999 else 'end'} of a spike: n={int(mk.sum()):3d} mean fwd21 {fwd[mk].mean():+.2%}") if mk.any() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ibs", action="store_true")
    ap.add_argument("--us-only", action="store_true")
    a = ap.parse_args()
    if a.us_only:
        return us_replication()
    f = m.build(m.load())
    print("=== 1. grid-free fear-buy ===")
    grid_free(f)
    print("\n=== 2. drawdown rival ===")
    drawdown_rival(f)
    entry_timing(f)
    if not a.skip_ibs:
        print("\n=== 3. IBS rotation overlap ===")
        ibs_overlap(f)
    us_replication()


def us_replication(delays=(0, 5, 10, 15), x=1.5):
    """Out-of-sample market for the persistence hypothesis: S&P 500 + VIX since
    1990 (includes 2000-02 and 2008, which the India sample (2009+) cannot).
    The delay grid and threshold are the ones the India post-hoc look produced,
    so a fail here is informative and a pass is a genuine replication."""
    import yfinance as yf

    def px(t):
        s = yf.Ticker(t).history(start="1990-01-01")["Close"]
        s.index = pd.DatetimeIndex(s.index.date)
        return s

    d = pd.DataFrame({"spx": px("^GSPC"), "vix": px("^VIX")}).dropna()
    rel = (d["vix"] / d["vix"].rolling(252).median()).to_numpy()
    close = d["spx"].to_numpy()
    fwd0 = np.full(len(d), np.nan)
    fwd0[: -HOLD - 1] = close[HOLD + 1 :][: len(d) - HOLD - 1] / close[1 : len(d) - HOLD] - 1
    pool = fwd0[~np.isnan(fwd0)]
    on = np.nan_to_num(rel) >= x
    first = declustered_events(on & ~np.isnan(fwd0))
    print(f"\n=== US replication: S&P 500 after VIX/252d-median >= {x} first crossing, 1990-2026 ({len(first)} events) ===")
    print(f"unconditional mean fwd21 {pool.mean():+.2%}, up {(pool > 0).mean():.0%}")
    half = len(first) // 2
    for k in delays:
        r = np.array([close[i + k + 1 + HOLD] / close[i + k + 1] - 1 for i in first if i + k + 1 + HOLD < len(close)])
        p = random_pool_p(pool, r)
        print(f"enter +{k:2d}d: mean {r.mean():+.2%}, median {np.median(r):+.2%}, positive {int((r > 0).sum())}/{len(r)}, "
              f"min {r.min():+.1%}, random-day p={p:.4f}; first half {r[:half].mean():+.2%} / second half {r[half:].mean():+.2%}")
    r10 = {d.index[i].date(): close[i + 11 + HOLD] / close[i + 11] - 1 for i in first if i + 11 + HOLD < len(close)}
    worst = sorted(r10.items(), key=lambda kv: kv[1])[:4]
    print("worst 4 events at +10d:", [(str(a), f"{b:+.1%}") for a, b in worst])


if __name__ == "__main__":
    main()
