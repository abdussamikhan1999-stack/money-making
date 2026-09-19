"""
Seventy-ninth entry: published versions of approaches associated with famous funds and traders, on
survivorship-free instruments (ETFs, indices) with long histories. Pre-registered here BEFORE running:

  1. TIME-SERIES MOMENTUM (the CTA core; Moskowitz-Ooi-Pedersen 2012, Winton/Man AHL style) across 11 ETFs
     (SPY EFA EEM VNQ TLT IEF LQD GLD SLV DBC UUP), monthly, look-back L in {126, 189, 252} trading days,
     60-day realised vol, two constructions:
       B. long/flat, inverse-vol weights, unlevered (sum of weights <= 1, rest earns T-bill)  - retail-tradable
       C. long/short, each asset sized to 10% annual vol / N (levered; RESEARCH ONLY - retail cannot run it)
     Control: a common circular rotation (>= 24 months) of the whole signal matrix against the returns
     (keeps the signals' cross-asset structure and time-in-market, breaks their timing).
  2. RISK PARITY (Bridgewater All Weather idea): inverse-vol weights over SPY TLT IEF GLD DBC, unlevered,
     monthly, vs equal weight and 60/40 (SPY/IEF). Descriptive (static weights have no timing to test).
  3. VOLATILITY-MANAGED EQUITY (Moreira-Muir 2019): exposure = min(1, target / 21-day realised vol), target in
     {10%, 15%}, unlevered, monthly; S&P 500 1960+ (T-bill cash) and NIFTY 2008+ (cash at a flat 5%).
     Control: rotation of the exposure vector against the returns.
  4. HALLOWEEN / "sell in May": hold equity Nov-Apr, cash May-Oct. S&P 500 1950+ and NIFTY 2008+.
     Statistic: mean(Nov-Apr months) - mean(May-Oct months), year-block bootstrap p.

Timing everywhere: decisions use data through the month-end close, the trade fills at the NEXT close (lag 1).
Costs: 0.05% per leg on turnover (ETF/index-fund scale). Portfolio return in a month:
    ret = cash + sum_j w_j (R_j - cash) - cost * turnover      (cash = T-bill return over the same days)
"""

import argparse
import functools

import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.0005
UNIVERSE = ["SPY", "EFA", "EEM", "VNQ", "TLT", "IEF", "LQD", "GLD", "SLV", "DBC", "UUP"]
RP_ASSETS = ["SPY", "TLT", "IEF", "GLD", "DBC"]


@functools.lru_cache(maxsize=None)
def hist(t, start):
    for _ in range(3):  # yfinance fails intermittently (see the Thirty-eighth/Forty-ninth entries)
        d = yf.Ticker(t).history(start=start)
        if len(d) and "Close" in d:
            break
    else:
        raise RuntimeError(f"{t}: no price data after 3 tries")
    x = d["Close"].dropna()
    x.index = pd.DatetimeIndex(x.index.date)
    return x[~x.index.duplicated()]


def month_end_rows(idx):
    ym = idx.to_period("M")
    return np.flatnonzero(ym != pd.Series(ym).shift(-1).to_numpy())[:-1]  # drop the (incomplete) last month


def tbill_index(idx, start):
    """Cumulative T-bill total-return index on idx from ^IRX (annualised percent yield)."""
    y = hist("^IRX", start).reindex(idx).ffill().bfill() / 100.0
    daily = (1 + y).pow(1 / 252) - 1
    return (1 + daily).cumprod()


def monthly_returns(px, cash_idx, me):
    """R[i, j]: asset return from the close AFTER month-end i to the close after month-end i+1; C[i]: cash."""
    p = px.to_numpy()
    c = np.asarray(cash_idx)
    a, b = me[:-1] + 1, me[1:] + 1
    ok = b < len(p)
    a, b = a[ok], b[ok]
    return p[b] / p[a] - 1, c[b] / c[a] - 1, me[:-1][ok]


def run_weights(W, R, C, cost=COST):
    """Unified return: cash + sum w (R - cash) - cost * turnover (drifted previous weights)."""
    n = len(W)
    out = np.zeros(n)
    prev = np.zeros(W.shape[1])
    for i in range(n):
        r = np.nan_to_num(R[i])
        w = np.nan_to_num(W[i])
        out[i] = C[i] + float((w * (r - C[i])).sum()) - cost * np.abs(w - prev).sum()
        gross = 1 + out[i]
        prev = w * (1 + r) / gross if gross > 0 else w
    return out


def stats(r, c=None):
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    yrs = len(r) / 12
    cagr = eq[-1] ** (1 / yrs) - 1
    dd = (1 - eq / peak).max()
    ex = r - (c if c is not None else 0.0)
    sharpe = ex.mean() / ex.std(ddof=1) * np.sqrt(12) if ex.std(ddof=1) > 0 else float("nan")
    return dict(cagr=cagr * 100, vol=r.std(ddof=1) * np.sqrt(12) * 100, sharpe=sharpe, dd=dd * 100,
                calmar=cagr / dd if dd > 0 else float("nan"), final=eq[-1])


def line(name, r, c=None):
    s = stats(r, c)
    return f"{name:34s} {s['cagr']:6.2f}%/yr, vol {s['vol']:5.1f}%, Sharpe {s['sharpe']:5.2f}, maxDD {s['dd']:5.1f}%, Calmar {s['calmar']:4.2f}"


def rotation_p(W, R, C, seeds=None, min_shift=24):
    """EXACT p for (Sharpe, final wealth): enumerate EVERY circular rotation >= min_shift away from 0 (the review
    found that 2,000 random draws from ~185 distinct rotations just repeat them and imply a false resolution).
    (count+1)/(n_rotations+1): the smallest reportable p is 1/(n_rotations+1)."""
    a = stats(run_weights(W, R, C), C)
    n = len(W)
    ks = range(min_shift, n - min_shift + 1)
    sh, fw = [], []
    for k in ks:
        s_ = stats(run_weights(np.roll(W, k, axis=0), R, C), C)
        sh.append(s_["sharpe"])
        fw.append(s_["final"])
    m = len(ks)
    return ((np.array(sh) >= a["sharpe"]).sum() + 1) / (m + 1), ((np.array(fw) >= a["final"]).sum() + 1) / (m + 1)


# ---------------------------------------------------------------- 1. time-series momentum
def tsmom(seeds):
    start = "2005-06-01"
    px = pd.DataFrame({t: hist(t, start) for t in UNIVERSE}).sort_index()
    px = px.dropna(how="all").ffill(limit=5)
    cash = tbill_index(px.index, start)
    me = month_end_rows(px.index)
    me = me[me >= 300]  # a year+ of history before the first decision
    R, C, rows = monthly_returns(px, cash, me)
    p = px.to_numpy()
    logret = np.log(px).diff().to_numpy()
    vol60 = (np.log(px).diff().rolling(60).std() * np.sqrt(252)).to_numpy()  # NaN until an asset has 60 returns
    n = len(rows)
    print(f"TSMOM: {len(UNIVERSE)} ETFs, {px.index[rows[0]].date()} .. {px.index[rows[-1]].date()}, {n} monthly decisions")
    bench = np.zeros((n, len(UNIVERSE)))
    for i, t in enumerate(rows):
        avail = ~np.isnan(p[t]) & ~np.isnan(p[t - 252])
        bench[i, avail] = 1 / max(avail.sum(), 1)
    print(line("equal-weight buy&hold (all)", run_weights(bench, R, C), C))
    WI = np.zeros((n, len(UNIVERSE)))  # the no-signal twin of construction B: always long, inverse-vol weights
    for i, t in enumerate(rows):
        vol = vol60[t]
        ok = ~np.isnan(p[t]) & ~np.isnan(p[t - 252]) & np.isfinite(vol) & (vol > 0)
        WI[i] = np.where(ok, 1 / np.where(ok, vol, 1), 0.0) / max((1 / np.where(ok, vol, 1))[ok].sum(), 1e-12)
    print(line("inverse-vol buy&hold (no signal)", run_weights(WI, R, C), C))
    for L in (126, 189, 252):
        WB = np.zeros((n, len(UNIVERSE)))
        WC = np.zeros((n, len(UNIVERSE)))
        for i, t in enumerate(rows):
            avail = ~np.isnan(p[t]) & ~np.isnan(p[t - L])
            sig = np.where(avail, np.sign(p[t] / np.where(avail, p[t - L], 1) - 1), 0.0)
            vol = np.where(np.isfinite(vol60[t]) & (vol60[t] > 0), vol60[t], np.nan)
            ok = avail & np.isfinite(vol)
            if ok.sum() == 0:
                continue
            inv = np.where(ok, 1 / vol, 0.0)
            WB[i] = np.where(sig > 0, inv, 0.0) / inv.sum()          # long/flat, unlevered (sum <= 1)
            WC[i] = np.where(ok, sig * (0.10 / np.where(ok, vol, 1)) / ok.sum(), 0.0)  # long/short, 10% vol per asset / N
        for name, W in (("B long/flat inv-vol (retail)", WB), ("C long/short 10%-vol (research)", WC)):
            r = run_weights(W, R, C)
            pS, pF = rotation_p(W, R, C, seeds)
            gross = np.abs(W).sum(1).mean()
            inmk = (np.abs(W).sum(1) > 0).mean()
            print(line(f"L={L} {name}", r, C) + f"; p(Sharpe)={pS:.3f} p(wealth)={pF:.3f}; mean gross {gross:.2f}, in-market {inmk:.0%}")


# ---------------------------------------------------------------- 2. risk parity
def riskparity():
    start = "2005-06-01"
    px = pd.DataFrame({t: hist(t, start) for t in RP_ASSETS}).dropna()
    cash = tbill_index(px.index, start)
    me = month_end_rows(px.index)
    me = me[me >= 300]
    R, C, rows = monthly_returns(px, cash, me)
    logret = np.log(px).diff().to_numpy()
    n = len(rows)
    print(f"\nRISK PARITY: {RP_ASSETS}, {px.index[rows[0]].date()} .. {px.index[rows[-1]].date()}, {n} months")
    W_rp = np.zeros((n, len(RP_ASSETS)))
    for i, t in enumerate(rows):
        vol = logret[t - 251 : t + 1].std(axis=0)
        W_rp[i] = (1 / vol) / (1 / vol).sum()
    W_eq = np.full((n, len(RP_ASSETS)), 1 / len(RP_ASSETS))
    W_64 = np.zeros((n, len(RP_ASSETS)))
    W_64[:, RP_ASSETS.index("SPY")], W_64[:, RP_ASSETS.index("IEF")] = 0.6, 0.4
    W_sp = np.zeros((n, len(RP_ASSETS)))
    W_sp[:, RP_ASSETS.index("SPY")] = 1.0
    res = {}
    for name, W in (("SPY buy&hold", W_sp), ("60/40 SPY/IEF", W_64), ("equal weight (5 assets)", W_eq), ("inverse-vol risk parity", W_rp)):
        r = run_weights(W, R, C)
        res[name] = r
        print(line(name, r, C))
    dates = px.index[rows + 1]  # R[i] is the return of the month AFTER decision i: label by the fill date
    for label, lo, hi in (("2008", "2008-01-01", "2008-12-31"), ("2013 (taper tantrum)", "2013-04-01", "2013-12-31"), ("2022 (stocks+bonds fell)", "2022-01-01", "2022-12-31")):
        m = (dates >= lo) & (dates <= hi)
        print(f"  {label:26s} " + " | ".join(f"{k}: {(np.prod(1 + v[m]) - 1) * 100:+6.1f}%" for k, v in res.items()))


# ---------------------------------------------------------------- 3. volatility-managed equity
def volmanaged(seeds):
    for name, tkr, start, cash_fixed in (("S&P 500 1960+", "^GSPC", "1959-06-01", None), ("NIFTY 2008+", "^NSEI", "2007-09-01", 0.05)):
        px = hist(tkr, start).to_frame("x")
        if cash_fixed is None:
            cash = tbill_index(px.index, start)
        else:
            days = np.concatenate([[0], (px.index[1:] - px.index[:-1]).days])
            cash = np.cumprod(1 + cash_fixed * days / 365.25)
        me = month_end_rows(px.index)
        me = me[me >= 260]
        R, C, rows = monthly_returns(px, cash, me)
        lr = np.log(px["x"]).diff().to_numpy()
        n = len(rows)
        print(f"\nVOL-MANAGED {name}: {px.index[rows[0]].date()} .. {px.index[rows[-1]].date()}, {n} months")
        print(line("buy&hold", run_weights(np.ones((n, 1)), R, C), C))
        for target in (0.10, 0.15):
            vol = np.array([lr[t - 20 : t + 1].std() * np.sqrt(252) for t in rows])
            W = np.minimum(1.0, target / vol)[:, None]
            r = run_weights(W, R, C)
            pS, pF = rotation_p(W, R, C, seeds, min_shift=12)
            print(line(f"target {target:.0%}, cap 1.0", r, C) + f"; mean exposure {W.mean():.2f}; p(Sharpe)={pS:.3f} p(wealth)={pF:.3f}")


# ---------------------------------------------------------------- 4. Halloween
def halloween(boots=10000):
    """Hold the index in the 6 winter months (Nov-Apr), cash otherwise, on the same lag-1 monthly machinery as the
    other sections (T-bill cash for the S&P, a flat 5% for NIFTY; 0.05% per leg on each switch; COMPLETE months
    only). The month is labelled by the calendar month of the FILL date. Gap statistic: mean winter-month return
    minus mean summer-month return per 12-month cycle (Nov s .. Oct s+1), year-block bootstrap."""
    rng = np.random.default_rng(0)
    for name, tkr, start, cash_fixed in (("S&P 500 1950+", "^GSPC", "1949-06-01", None), ("NIFTY 2008+", "^NSEI", "2007-09-01", 0.05)):
        px = hist(tkr, start).to_frame("x")
        if cash_fixed is None:
            cash = tbill_index(px.index, start)
        else:
            days = np.concatenate([[0], (px.index[1:] - px.index[:-1]).days])
            cash = np.cumprod(1 + cash_fixed * days / 365.25)
        me = month_end_rows(px.index)
        me = me[me >= 20]
        R, C, rows = monthly_returns(px, cash, me)
        month = px.index[rows + 1].month.to_numpy()   # calendar month of the fill date
        year = px.index[rows + 1].year.to_numpy()
        winter = np.isin(month, [11, 12, 1, 2, 3, 4])
        r = R[:, 0]
        season = np.where(month >= 11, year, year - 1)  # the winter cycle starting in November of `season`
        df = pd.DataFrame({"r": r, "w": winter, "season": season, "year": year})
        w_mean = df[df.w].groupby("season")["r"].mean()
        s_mean = df[~df.w].groupby("year")["r"].mean()
        s_mean.index = s_mean.index - 1  # the May-Oct that follows the winter starting Nov s
        d = (w_mean - s_mean.reindex(w_mean.index)).dropna()
        boot = np.array([rng.choice(d.to_numpy(), size=len(d), replace=True).mean() for _ in range(boots)])
        p = ((boot <= 0).sum() + 1) / (boots + 1)
        W = winter.astype(float)[:, None]
        strat = run_weights(W, R, C)
        bh = run_weights(np.ones((len(r), 1)), R, C)
        print(f"\nHALLOWEEN {name}: {len(d)} cycles | mean monthly winter {r[winter].mean():+.2%}, summer {r[~winter].mean():+.2%}, "
              f"gap {d.mean():+.2%}/mo (year-block bootstrap p={p:.4f}, gap>0 in {(d > 0).mean():.0%} of cycles)")
        print(line("  buy&hold", bh, C))
        print(line("  Nov-Apr only (cash May-Oct)", strat, C))
        mid = len(d) // 2
        print(f"  first half gap {d.iloc[:mid].mean():+.2%}, second half {d.iloc[mid:].mean():+.2%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    ap.add_argument("--only", choices=("tsmom", "riskparity", "volmanaged", "halloween"))
    a = ap.parse_args()
    if a.only in (None, "tsmom"):
        tsmom(a.seeds)
    if a.only in (None, "riskparity"):
        riskparity()
    if a.only in (None, "volmanaged"):
        volmanaged(a.seeds)
    if a.only in (None, "halloween"):
        halloween()


if __name__ == "__main__":
    main()
