"""
Macro / geopolitical regime probe (Fifty-seventh entry).

Three candidates and their perturbation grids were FIXED before any outcome
was looked at (see CLAUDE.md Fifty-seventh entry), because the Fifty-first
entry's council flagged retrofitted-story risk: after a geopolitical shock
it is easy to pick the analog episodes that "worked."

  A. Analog forecaster: at each monthly decision date, find the k historical
     dates whose 8-feature macro state is nearest (features standardised with
     data <= t only, candidate dates whose forward window had already closed
     by t only, declustered) and go long NIFTY for the next 21 trading days
     iff the analogs' mean forward return beats the expanding base rate.
  B. Oil+rupee stress filter: flat NIFTY while Brent 60d return >= B and
     USDINR 60d change >= I, else long.
  C. Fear buy: long NIFTY 21 days after a VIX spike (VIX / trailing-252d
     median >= X), declustered.

Every candidate gets the same random-timing control as the Fifty-sixth entry
(circularly rotate the signal against returns, 1,500 seeds) because a
long-only NIFTY rule earns drift for free.

Timing: NIFTY closes before US markets/ICE Brent/INR=X, so every non-Indian
series is shifted one day before use, and the trade fills at the NEXT
close after the decision date. No same-bar fills anywhere.
"""

import argparse
import datetime
import os
import random

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_HERE, "macro_cache.csv")            # committed, reproducible backtest data
LIVE_CACHE = os.path.join(_HERE, "macro_cache_live.csv")  # refreshed by --state-only (git-ignored)
OFFSET = 0  # grid phase of the non-overlapping monthly decision rows
COST_ROUNDTRIP = 0.004  # 0.2% each way, same equity cost model as prior probes
HOLD = 21
FEATURES = ["brent60", "inr60", "nifty60", "nifty_dd", "ivix", "vix", "us10y60", "dxy60"]


def download():
    import yfinance as yf

    tk = {"nifty": "^NSEI", "ivix": "^INDIAVIX", "spx": "^GSPC", "vix": "^VIX", "brent": "BZ=F",
          "gold": "GC=F", "inr": "INR=X", "dxy": "DX-Y.NYB", "us10y": "^TNX"}
    cols = {k: yf.Ticker(v).history(start="2007-01-01")["Close"] for k, v in tk.items()}
    for s in cols.values():
        s.index = pd.DatetimeIndex(s.index.date)
    return pd.DataFrame(cols)


def load(refresh=False):
    """refresh=True (used by --state-only): redownload, drop a still-forming today bar, save to the live cache
    (never the committed one), and fall back to the committed cache with a warning if the network fails.
    Review finding: without this the monthly state report silently printed stale data forever."""
    if refresh:
        try:
            df = download()
            today = pd.Timestamp(datetime.date.today())
            if df.index[-1] >= today and datetime.datetime.now().hour < 16:  # NSE closes 15:30 IST
                df = df.iloc[:-1]
            df.to_csv(LIVE_CACHE)
            return df
        except Exception as e:  # noqa: BLE001 - report and fall back, never crash a state report
            print(f"WARNING: refresh failed ({e}); using the committed cache, which may be stale")
    if os.path.exists(CACHE):
        return pd.read_csv(CACHE, index_col=0, parse_dates=True)
    df = download()
    df.to_csv(CACHE)
    return df


def build(df):
    """Feature frame on NIFTY's trading calendar. Non-Indian series lagged 1 day."""
    d = df.copy()
    nifty = d["nifty"].dropna()
    d = d.reindex(nifty.index).ffill(limit=5)
    for c in ["brent", "inr", "vix", "us10y", "dxy", "spx", "gold"]:
        d[c] = d[c].shift(1)
    f = pd.DataFrame(index=d.index)
    f["nifty"] = d["nifty"]
    f["brent60"] = d["brent"].pct_change(60)
    f["inr60"] = d["inr"].pct_change(60)
    f["nifty60"] = d["nifty"].pct_change(60)
    f["nifty_dd"] = d["nifty"] / d["nifty"].rolling(252).max() - 1
    f["ivix"] = d["ivix"]
    f["vix"] = d["vix"]
    f["us10y60"] = d["us10y"].diff(60)
    f["dxy60"] = d["dxy"].pct_change(60)
    f["ivix_rel"] = d["ivix"] / d["ivix"].rolling(252).median()
    f["vix_rel"] = d["vix"] / d["vix"].rolling(252).median()
    # forward return of the window that STARTS the day after the decision date
    f["fwd"] = d["nifty"].shift(-(HOLD + 1)) / d["nifty"].shift(-1) - 1
    return f


def analog_forecast(f, t_idx, k, hold=HOLD, decluster=20):
    """Mean forward return of the k nearest declustered analogs to row t_idx,
    using only rows whose forward window closed by t_idx and stats from <= t_idx."""
    X = f[FEATURES].to_numpy()
    fwd = f["fwd"].to_numpy()
    hist_end = t_idx - (hold + 1)  # last usable candidate row
    Xh = X[: hist_end + 1]
    ok = ~np.isnan(Xh).any(axis=1) & ~np.isnan(fwd[: hist_end + 1])
    cur = X[t_idx]
    if np.isnan(cur).any() or ok.sum() < 500:
        return None, None
    sub = X[: t_idx + 1]
    sub = sub[~np.isnan(sub).any(axis=1)]
    mu, sd = sub.mean(0), sub.std(0) + 1e-12
    dist = np.sqrt((((Xh - mu) / sd - (cur - mu) / sd) ** 2).sum(1))
    dist[~ok] = np.inf
    chosen = []
    for i in np.argsort(dist):
        if not np.isfinite(dist[i]) or len(chosen) >= k:
            break
        if all(abs(i - j) > decluster for j in chosen):
            chosen.append(i)
    if len(chosen) < k:
        return None, None
    base = np.nanmean(fwd[: hist_end + 1][ok])
    return float(np.mean(fwd[chosen])), float(base)


def pnl_from_signal(sig, rets):
    sig = np.asarray(sig, float)
    prev = np.concatenate([[0.0], sig[:-1]])
    trans = np.abs(sig - prev)  # each 0<->1 flip pays one leg
    return float((sig * rets).sum() - (COST_ROUNDTRIP / 2) * trans.sum())


def shift_p(sig, rets, n_seeds, seed=0, min_shift=12):
    actual = pnl_from_signal(sig, rets)
    rng = random.Random(seed)
    n = len(sig)
    sig = np.asarray(sig)
    tot = []
    for _ in range(n_seeds):
        k = rng.randrange(min_shift, n - min_shift)
        tot.append(pnl_from_signal(np.roll(sig, k), rets))
    return actual, float(np.mean(tot)), (sum(1 for t in tot if t >= actual) + 1) / (n_seeds + 1)  # (count+1)/(n+1): never exactly 0


def subset_p(sig, rets, n_draws=200_000, seed=1):
    """Permutation p with an unbounded state space: draw the same NUMBER of 'on'
    months uniformly at random and compare mean fwd return. Ignores clustering
    of triggers in time (kinder than the rotation control) but, unlike a
    rotation of n~160 months (~136 distinct offsets => p floor ~0.0073),
    can resolve small p-values. (count+1)/(n+1) convention, never exactly 0."""
    rng = np.random.default_rng(seed)
    sig = np.asarray(sig, bool)
    rets = np.asarray(rets, float)
    k = int(sig.sum())
    actual = rets[sig].mean()
    idx = np.argsort(rng.random((n_draws, len(rets))), axis=1)[:, :k]
    means = rets[idx].mean(axis=1)
    return (int((means >= actual).sum()) + 1) / (n_draws + 1)


def quarters(pnls_by_date):
    idx = pnls_by_date.index
    edges = pd.date_range(idx[0], idx[-1], periods=5)
    return [float(pnls_by_date[(idx >= edges[q]) & (idx <= edges[q + 1])].sum()) for q in range(4)]


def decisions(f, start="2013-01-01"):
    """Non-overlapping monthly decision rows."""
    rows = [i for i in range(len(f)) if f.index[i] >= pd.Timestamp(start)]
    return rows[OFFSET::HOLD]


def test_analog(f, k, seeds):
    rows = [r for r in decisions(f) if not np.isnan(f["fwd"].iloc[r])]
    sig, rets, dates, fc = [], [], [], []
    for r in rows:
        m, base = analog_forecast(f, r, k)
        if m is None:
            continue
        sig.append(1.0 if m > base else 0.0)
        fc.append(m - base)
        rets.append(f["fwd"].iloc[r])
        dates.append(f.index[r])
    rets = np.array(rets)
    ic = pd.Series(fc).corr(pd.Series(rets), method="spearman")
    actual, rmean, p = shift_p(sig, rets, seeds)
    ser = pd.Series(np.array(sig) * rets, index=dates)
    return dict(n=len(sig), in_mkt=float(np.mean(sig)), ic=float(ic), pnl=actual, rand=rmean, p=p,
                bh=float(rets.sum()), q=quarters(ser))


def test_stress(f, b, i, seeds):
    rows = [r for r in decisions(f) if not np.isnan(f["fwd"].iloc[r])]
    stress = [(f["brent60"].iloc[r] >= b) and (f["inr60"].iloc[r] >= i) for r in rows]
    sig = [0.0 if s else 1.0 for s in stress]
    rets = np.array([f["fwd"].iloc[r] for r in rows])
    actual, rmean, p = shift_p(sig, rets, seeds)
    ser = pd.Series(np.array(sig) * rets, index=[f.index[r] for r in rows])
    on = np.array(stress)
    return dict(n=len(sig), n_stress=int(on.sum()),
                mean_stress=float(rets[on].mean()) if on.any() else float("nan"),
                mean_calm=float(rets[~on].mean()), pnl=actual, rand=rmean, p=p,
                bh=float(rets.sum()), q=quarters(ser))


def test_fear(f, col, x, seeds):
    """Long HOLD days after a spike; check each month whether spike is on."""
    rows = [r for r in decisions(f) if not np.isnan(f["fwd"].iloc[r])]
    sig = [1.0 if f[col].iloc[r] >= x else 0.0 for r in rows]
    rets = np.array([f["fwd"].iloc[r] for r in rows])
    if sum(sig) < 3:
        return dict(n=len(sig), n_on=int(sum(sig)), p=float("nan"))
    actual, rmean, p = shift_p(sig, rets, seeds)
    ser = pd.Series(np.array(sig) * rets, index=[f.index[r] for r in rows])
    return dict(n=len(sig), n_on=int(sum(sig)), pnl=actual, rand=rmean, p=p, q=quarters(ser),
                p_subset=subset_p(sig, rets, n_draws=20_000))


def state_report(f, k=8):
    """Descriptive only: today's state, and its k nearest historical analogs."""
    t = len(f) - 1
    X = f[FEATURES]
    cur = X.iloc[t]
    sub = X.dropna()
    z = (cur - sub.mean()) / sub.std()
    print(f"As of {f.index[t].date()}  NIFTY {f['nifty'].iloc[t]:,.0f}")
    print(f"{'feature':10s} {'now':>9s} {'z':>6s} {'pctile':>7s}")
    for c in FEATURES:
        pct = (sub[c] <= cur[c]).mean() * 100
        print(f"{c:10s} {cur[c]:9.3f} {z[c]:6.2f} {pct:6.0f}%")
    print(f"fear-buy trigger status (candidate C, fires at >=1.5): india-VIX/252d-median "
          f"{f['ivix_rel'].iloc[t]:.2f}, US-VIX/252d-median {f['vix_rel'].iloc[t]:.2f}")
    px = f["nifty"]
    smas = {L: px.rolling(L).mean().iloc[-1] for L in (100, 150, 200)}
    print("IBS-rotation trend gate (Sixty-first entry; cash when NIFTY < SMA at the month-end ranking close): "
          f"NIFTY {px.iloc[-1]:,.0f} vs " + ", ".join(f"SMA{L} {v:,.0f} -> {'RISK-ON' if px.iloc[-1] > v else 'RISK-OFF'}" for L, v in smas.items()))
    mu, sd = sub.mean().to_numpy(), sub.std().to_numpy()
    Xa = X.to_numpy()
    ok = ~np.isnan(Xa).any(axis=1)
    dist = np.sqrt((((Xa - mu) / sd - ((cur.to_numpy() - mu) / sd)) ** 2).sum(1))
    dist[~ok] = np.inf
    dist[t - 21:] = np.inf  # exclude the last month itself
    chosen = []
    for i in np.argsort(dist):
        if len(chosen) >= k or not np.isfinite(dist[i]):
            break
        if all(abs(i - j) > 40 for j in chosen):
            chosen.append(i)
    print("\nNearest historical analogs (dist, date, then each feature's z-gap vs today, fwd 21d/63d NIFTY):")
    for i in sorted(chosen, key=lambda j: dist[j]):
        gap = ((Xa[i] - mu) / sd - (cur.to_numpy() - mu) / sd)
        big = sorted(zip(FEATURES, gap), key=lambda x: -abs(x[1]))[:2]
        f21 = f["nifty"].iloc[min(i + 22, t)] / f["nifty"].iloc[i + 1] - 1 if i + 22 <= t else float("nan")
        f63 = f["nifty"].iloc[i + 64] / f["nifty"].iloc[i + 1] - 1 if i + 64 <= t else float("nan")
        print(f"  {dist[i]:5.2f} {f.index[i].date()}  biggest differences: "
              + ", ".join(f"{n} {g:+.1f}z" for n, g in big) + f"   fwd21 {f21:+.1%}  fwd63 {f63:+.1%}")


def robustness(f, seeds):
    """Grid-phase, concentration and feature-ablation checks (added after the
    first pass showed p~0 for the fear-buy, which needed explaining)."""
    try:
        return _robustness(f, seeds)
    finally:  # module globals are mutated below; always restore them
        globals()["OFFSET"] = 0
        FEATURES[:] = ["brent60", "inr60", "nifty60", "nifty_dd", "ivix", "vix", "us10y60", "dxy60"]


def _robustness(f, seeds):
    global OFFSET
    print("\n=== grid-phase robustness (21 offsets) ===")
    res = {"A k=15": [], "C ivix>=1.5": [], "C vix>=1.5": []}
    for o in range(0, HOLD, 3):
        OFFSET = o
        res["A k=15"].append(test_analog(f, 15, 300)["p"])
        res["C ivix>=1.5"].append(test_fear(f, "ivix_rel", 1.5, 300)["p"])
        res["C vix>=1.5"].append(test_fear(f, "vix_rel", 1.5, 300)["p"])
    for k, v in res.items():
        print(f"{k}: p by offset {[round(x, 3) for x in v]}")
    OFFSET = 0
    print("\n=== concentration: fear-buy P&L by calendar year and without best episodes ===")
    rows = [r for r in decisions(f) if not np.isnan(f["fwd"].iloc[r])]
    for col in ("ivix_rel", "vix_rel"):
        on = [(f.index[r], f["fwd"].iloc[r]) for r in rows if f[col].iloc[r] >= 1.5]
        rets = sorted((x for _, x in on), reverse=True)
        yrs = {}
        for d, x in on:
            yrs[d.year] = yrs.get(d.year, 0) + x
        print(f"{col}>=1.5: {len(on)} months, sum {sum(rets):+.1%}, w/o best 1 {sum(rets[1:]):+.1%}, "
              f"w/o best 3 {sum(rets[3:]):+.1%}, negative months {sum(1 for x in rets if x < 0)}; "
              f"by year { {y: f'{v:+.1%}' for y, v in sorted(yrs.items())} }")
    print("\n=== A ablation (k=15): which feature group carries the analog signal? ===")
    full = list(FEATURES)
    groups = {"all 8": full, "vol only (ivix,vix)": ["ivix", "vix"],
              "geo-macro only (brent,inr,us10y,dxy)": ["brent60", "inr60", "us10y60", "dxy60"],
              "no vol (drop ivix,vix)": [c for c in full if c not in ("ivix", "vix")],
              "nifty state only (nifty60,dd)": ["nifty60", "nifty_dd"]}
    for name, cols in groups.items():
        FEATURES[:] = cols
        r = test_analog(f, 15, seeds)
        print(f"{name:40s}: in-mkt {r['in_mkt']:.0%}, rank-IC {r['ic']:+.3f}, P&L {r['pnl']:+.1%} vs random "
              f"{r['rand']:+.1%}, p={r['p']:.4f}")
    FEATURES[:] = full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1500)
    ap.add_argument("--state-only", action="store_true")
    ap.add_argument("--robust", action="store_true")
    ap.add_argument("--episodes", action="store_true")
    a = ap.parse_args()
    f = build(load(refresh=a.state_only))
    if a.episodes:
        return oil_shock_episodes()
    state_report(f)
    if a.state_only:
        return
    if a.robust:
        return robustness(f, a.seeds)
    print("\n=== A. analog forecaster (k perturbation) ===")
    for k in (8, 15, 30):
        r = test_analog(f, k, a.seeds)
        print(f"k={k:2d}: {r['n']} decisions, in-mkt {r['in_mkt']:.0%}, rank-IC {r['ic']:+.3f}, "
              f"P&L {r['pnl']:+.2%} vs random mean {r['rand']:+.2%} (p={r['p']:.3f}); "
              f"buy&hold-sum {r['bh']:+.2%}; quarters {[f'{x:+.1%}' for x in r['q']]}")
    print("\n=== B. oil+rupee stress filter (flat while stressed) ===")
    for b in (0.20, 0.30):
        for i in (0.01, 0.03):
            r = test_stress(f, b, i, a.seeds)
            print(f"brent60>={b:.0%} & inr60>={i:.0%}: stress in {r['n_stress']}/{r['n']} months, "
                  f"mean fwd21 stress {r['mean_stress']:+.2%} vs calm {r['mean_calm']:+.2%}; "
                  f"P&L {r['pnl']:+.2%} vs random {r['rand']:+.2%} (p={r['p']:.3f})")
    print("\n=== C. fear buy (long 21d after VIX spike) ===")
    for col in ("ivix_rel", "vix_rel"):
        for x in (1.4, 1.5, 1.7):
            r = test_fear(f, col, x, a.seeds)
            if r["n_on"] < 3:
                print(f"{col}>={x}: only {r['n_on']} triggers, untestable")
            else:
                print(f"{col}>={x}: {r['n_on']}/{r['n']} months on; P&L {r['pnl']:+.2%} vs random "
                      f"{r['rand']:+.2%} (rotation p={r['p']:.4f}, floor ~{1/(r['n']-23):.4f}; "
                      f"random-subset p={r['p_subset']:.5f}); quarters {[f'{q:+.1%}' for q in r['q']]}")



def oil_shock_episodes():
    """Mechanical, hand-pick-free: first day dated-Brent (FRED, 1987+) is at a
    252d high AND +40% over 60 trading days, declustered 120 trading days.
    Descriptive only (n is single digits): forward SPX/Sensex returns + the
    macro backdrop each time, versus today's. No p-value is claimed."""
    import io
    import urllib.request

    import yfinance as yf

    raw = urllib.request.urlopen("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU", timeout=30).read()
    b = pd.read_csv(io.BytesIO(raw), index_col=0, parse_dates=True).iloc[:, 0]
    b = pd.to_numeric(b, errors="coerce").dropna()
    hist = {k: yf.Ticker(v).history(start="1960-01-01")["Close"] for k, v in
            {"spx": "^GSPC", "sensex": "^BSESN", "vix": "^VIX", "y10": "^TNX"}.items()}
    for s in hist.values():
        s.index = pd.DatetimeIndex(s.index.date)
    r60 = b.pct_change(60)
    hi = b >= b.rolling(252).max()
    ev = b.index[(r60 >= 0.40) & hi]
    picked, last = [], None
    for d in ev:
        if last is None or (d - last).days > 170:
            picked.append(d)
            last = d
    print(f"\n=== oil-shock episodes (dated Brent +40%/60d at a 252d high, declustered) ===")
    print(f"{'date':10s} {'brent':>6s} {'60d':>5s} {'VIX':>5s} {'10y':>5s} | SPX fwd 21d/63d/126d/252d | Sensex fwd 63d/126d/252d")

    def fwd(s, d, n):
        s = s.dropna()
        i = s.index.searchsorted(d)
        return s.iloc[i + n] / s.iloc[i] - 1 if i + n < len(s) else float("nan")

    def at(s, d):
        s = s.dropna()
        i = s.index.searchsorted(d)
        return s.iloc[min(i, len(s) - 1)]

    for d in picked:
        sp = "/".join(f"{fwd(hist['spx'], d, n):+.0%}" for n in (21, 63, 126, 252))
        sx = "/".join(f"{fwd(hist['sensex'], d, n):+.0%}" for n in (63, 126, 252)) if d >= pd.Timestamp("1997-08-01") else "n/a"
        vix = at(hist["vix"], d) if d >= pd.Timestamp("1990-02-01") else float("nan")
        print(f"{d.date()} {b[d]:6.1f} {r60[d]:+5.0%} {vix:5.1f} {at(hist['y10'], d):5.2f} | {sp:>26s} | {sx}")
    now = b.index[-1]
    print(f"NOW        {b.iloc[-1]:6.1f} {r60.iloc[-1]:+5.0%} {hist['vix'].iloc[-1]:5.1f} {hist['y10'].iloc[-1]:5.2f}   (dated Brent as of {now.date()})")


if __name__ == "__main__":
    main()
