"""
Sixty-ninth/Seventieth entry: multi-asset ETF rotation on NSE (NIFTYBEES equity, GOLDBEES gold, LIQUIDBEES
cash proxy). Survivorship-free (the funds exist and always did) and not blocked by any lot-size wall.

Pre-registered before running (docstring fixed first):
  A. Dual momentum (Antonacci-style): at each month-end pick the higher trailing-L-month-return of
     NIFTYBEES vs GOLDBEES; if that return is <= 0 hold cash (LIQUIDBEES). L in {6, 9, 12}.
  B. Per-asset trend gating: hold each of NIFTYBEES/GOLDBEES (50/50) only while its close > its L-day SMA,
     the off half sits in cash. L in {100, 150, 200}.
Decisions at month-end closes, filled at the NEXT close, 0.1% per leg cost on every change of holding.
Baselines: NIFTYBEES, GOLDBEES, 50/50 monthly-rebalanced. Control: circular rotation of the decision
series against the returns (>=12 months), which keeps how often each holding is chosen but breaks its timing.
"""

import argparse

import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.001  # per leg


def fetch(t):
    s = yf.Ticker(t).history(start="2008-06-01")["Close"].dropna()
    s.index = pd.DatetimeIndex(s.index.date)
    return s


def load():
    px = pd.DataFrame({"N": fetch("NIFTYBEES.NS"), "G": fetch("GOLDBEES.NS"), "C": fetch("LIQUIDBEES.NS")}).dropna()
    return px


def month_ends(idx):
    ym = idx.to_period("M")
    return np.flatnonzero(ym != pd.Series(ym).shift(-1).to_numpy())


def run(px, weights, me):
    """weights: array [n_months, 3] (N, G, C) chosen at month-end me[i], held from the next close to the
    next month's next close. Returns monthly net returns (cost on turnover) and the series index."""
    p = px.to_numpy()
    rets, prev = [], np.array([0.0, 0.0, 1.0])
    for i in range(len(me) - 1):
        a, b = me[i] + 1, me[i + 1] + 1
        if b >= len(p):
            break
        r = p[b] / p[a] - 1
        w = weights[i]
        turnover = np.abs(w - prev).sum()
        rets.append(float((w * r).sum() - COST * turnover))
        prev = w * (1 + r) / (w * (1 + r)).sum()  # weights DRIFT over the month, so a static 50/50 pays its rebalancing cost
    return np.array(rets)


def stats(r):
    eq = np.cumprod(np.concatenate([[1.0], 1 + r]))[1:]
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]  # starting capital 1.0 is part of the peak
    yrs = len(r) / 12
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    dd = (1 - eq / peak).max()
    return cagr, dd, eq[-1]


def line(name, r):
    cagr, dd, _ = stats(r)
    h = len(r) // 2
    q = [np.prod(1 + x) - 1 for x in np.array_split(r, 4)]
    return (f"{name:34s} {cagr:6.2f}%/yr, maxDD {dd:5.1%}, Calmar {cagr / (100 * dd):4.2f}, halves "
            f"{np.prod(1 + r[:h]) - 1:+.0%}/{np.prod(1 + r[h:]) - 1:+.0%}, quarters {[f'{x:+.0%}' for x in q]}")


def rotation_p(px, weights, me, seeds=2000):
    actual = stats(run(px, weights, me))
    rng = np.random.default_rng(0)
    n = len(weights)
    fin, cal = [], []
    for _ in range(seeds):
        k = int(rng.integers(12, n - 12))
        c, d, f = stats(run(px, np.roll(weights, k, axis=0), me))
        fin.append(f)
        cal.append(c / (100 * d))
    a_cal = actual[0] / (100 * actual[1])
    return ((np.array(fin) >= actual[2]).sum() + 1) / (seeds + 1), ((np.array(cal) >= a_cal).sum() + 1) / (seeds + 1)


def descriptive(px, me):
    """Is the static 50/50's result a diversification property or just gold's bull run? Sub-periods split at
    gold's 2013 crash and 2019 breakout, monthly-return correlation, and worst rolling 3-year windows."""
    n = len(me) - 1
    p = px.to_numpy()
    H = np.tile([0.5, 0.5, 0.0], (n, 1))
    rn = run(px, np.tile([1.0, 0.0, 0.0], (n, 1)), me)
    rg = run(px, np.tile([0.0, 1.0, 0.0], (n, 1)), me)
    rh = run(px, H, me)
    dates = px.index[me[:len(rh)]]
    print(f"\nmonthly-return correlation equity vs gold: {np.corrcoef(rn, rg)[0, 1]:.2f}")
    for label, lo, hi in (("2009-2013 (gold bull)", "2009-01-01", "2013-09-30"), ("2013-10..2019-05 (gold flat)", "2013-10-01", "2019-05-31"),
                          ("2019-06..2026 (gold bull)", "2019-06-01", "2026-12-31")):
        m = (dates >= lo) & (dates <= hi)
        f = lambda r: ((np.prod(1 + r[m]) ** (12 / m.sum()) - 1) * 100)
        dd = lambda r: (1 - np.cumprod(1 + r[m]) / np.maximum.accumulate(np.cumprod(1 + r[m]))).max()
        print(f"  {label:32s} ({int(m.sum()):3d} mo): equity {f(rn):6.2f}%/yr DD {dd(rn):.1%} | gold {f(rg):6.2f}%/yr DD {dd(rg):.1%} | 50/50 {f(rh):6.2f}%/yr DD {dd(rh):.1%}")
    for name, r in (("equity", rn), ("gold", rg), ("50/50", rh)):
        roll = np.array([(np.prod(1 + r[i:i + 36]) ** (1 / 3) - 1) * 100 for i in range(len(r) - 35)])
        print(f"  rolling 3y CAGR {name:8s}: worst {roll.min():6.2f}%, median {np.median(roll):6.2f}%, share of windows < 5%/yr {np.mean(roll < 5):.0%}")


def multi(seeds):
    """Seventy-first entry (pre-registered): cross-sectional rotation across 4 ETFs (NIFTYBEES, BANKBEES,
    JUNIORBEES, GOLDBEES): momentum L in {6, 12} months and 1-month reversal, top_k in {1, 2}, monthly,
    next-close fill, 0.1% per leg on changes. Control: k random ETFs each month (same costs)."""
    px = pd.DataFrame({t: fetch(t + ".NS") for t in ("NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES")}).dropna()
    me = month_ends(px.index)
    me = me[me >= 260]
    p = px.to_numpy()
    n = len(me) - 1
    print(f"\n{px.index[0].date()} .. {px.index[-1].date()}, 4 ETFs, {n} monthly decisions")

    def run_w(W):
        rets, prev = [], np.zeros(4)
        for i in range(n):
            a, b = me[i] + 1, me[i + 1] + 1
            if b >= len(p):
                break
            rets.append(float((W[i] * (p[b] / p[a] - 1)).sum() - COST * np.abs(W[i] - prev).sum()))
            r_ = p[b] / p[a] - 1
            prev = W[i] * (1 + r_) / (W[i] * (1 + r_)).sum() if W[i].sum() else W[i]
        return np.array(rets)

    def weights(kind, L, k):
        W = np.zeros((n, 4))
        for i in range(n):
            t = me[i]
            r = p[t] / p[t - 21 * L] - 1
            order = np.argsort(-r) if kind == "mom" else np.argsort(r)
            W[i, order[:k]] = 1.0 / k
        return W

    eqw = run_w(np.full((n, 4), 0.25))
    print(line("equal-weight all four (monthly)", eqw))
    for kind, L, k in (("mom", 6, 1), ("mom", 6, 2), ("mom", 12, 1), ("mom", 12, 2), ("rev", 1, 1), ("rev", 1, 2)):
        r = run_w(weights(kind, L, k))
        rng = np.random.default_rng(0)
        fin = []
        for _ in range(seeds):
            W = np.zeros((n, 4))
            for i in range(n):
                W[i, rng.choice(4, k, replace=False)] = 1.0 / k
            fin.append(stats(run_w(W))[2])
        pv = ((np.array(fin) >= stats(r)[2]).sum() + 1) / (seeds + 1)
        print(line(f"{kind} L={L}m top_k={k}", r) + f"; random-pick mean {((np.mean(fin) ** (1 / (len(r) / 12)) - 1) * 100):.2f}%/yr, p={pv:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    ap.add_argument("--multi", action="store_true")
    ap.add_argument("--alloc", action="store_true", help="descriptive static allocation grid on --proxy data")
    ap.add_argument("--proxy", choices=("nifty", "sensex", "spx"), help="long-history proxies instead of the NSE ETFs")
    a = ap.parse_args()
    if a.multi:
        return multi(min(a.seeds, 1000))
    if a.alloc:
        for k in ("sensex", "spx"):
            alloc_grid(proxy_prices(k), k)
        return
    if a.proxy:
        return evaluate(proxy_prices(a.proxy), a.seeds, label=a.proxy)
    return evaluate(load(), a.seeds, label="NSE ETFs")


def alloc_grid(px, label):
    """Descriptive static-allocation grid (no timing, so no p-values): equity/gold weights and rebalance
    frequency, month-end closes, 0.1% per leg on the rebalancing turnover. Annual = December month-ends."""
    me = month_ends(px.index)
    p = px.to_numpy()[me]
    dates = px.index[me]
    rel = p[1:] / p[:-1]          # monthly price relatives [N, G, C]
    print(f"\n[{label}] static allocation grid, {dates[0].date()} .. {dates[-1].date()}, {len(rel)} months")
    for name, w0 in (("30% equity / 70% gold", (0.3, 0.7, 0.0)), ("50/50", (0.5, 0.5, 0.0)), ("70% equity / 30% gold", (0.7, 0.3, 0.0)),
                     ("40/40/20 cash", (0.4, 0.4, 0.2))):
        for freq in ("monthly", "annual"):
            w0a = np.array(w0)
            hold = w0a.copy()                       # current weights (drift between rebalances)
            rets = []
            for i, r in enumerate(rel):
                if freq == "monthly" or dates[i].month == 12 or i == 0:
                    turnover = np.abs(w0a - hold).sum()
                    cost = COST * turnover if i > 0 else 0.0
                    hold = w0a.copy()
                else:
                    cost = 0.0
                gross = float((hold * (r - 1)).sum())
                rets.append(gross - cost)
                hold = hold * r / (hold * r).sum()  # drift
            rets = np.array(rets)
            cagr, dd, _ = stats(rets)
            roll = np.array([(np.prod(1 + rets[i:i + 36]) ** (1 / 3) - 1) * 100 for i in range(len(rets) - 35)])
            print(f"  {name:22s} {freq:7s}: {cagr:5.2f}%/yr, maxDD {dd:5.1%}, Calmar {cagr / (100 * dd):4.2f}, worst rolling 3y {roll.min():6.2f}%, "
                  f"windows <5%/yr {np.mean(roll < 5):.0%}")


def proxy_prices(kind, cash_rate=0.04):
    """Long-history stand-ins (Seventy-second entry): 'nifty' = ^NSEI + gold in INR (GC=F x INR=X), 2007+;
    'spx' = ^GSPC + GC=F in USD, 2000+. Price indices (no dividends); cash accrues at a flat 4%/yr."""
    def h(t):
        x = yf.Ticker(t).history(start="2000-01-01")["Close"].dropna()
        x.index = pd.DatetimeIndex(x.index.date)
        return x
    gold = h("GC=F")
    if kind in ("nifty", "sensex"):
        eq, fx = h("^NSEI" if kind == "nifty" else "^BSESN"), h("INR=X")
        df = pd.DataFrame({"N": eq, "G": gold, "F": fx}).dropna()
        df["G"] = df["G"] * df["F"]
        df = df.drop(columns="F")
    else:
        df = pd.DataFrame({"N": h("^GSPC"), "G": gold}).dropna()
    days = (df.index[1:] - df.index[:-1]).days
    df["C"] = np.concatenate([[1.0], np.cumprod(1 + cash_rate * days / 365.25)])
    return df


def evaluate(px, seeds, label=""):
    me = month_ends(px.index)
    me = me[me >= 260]  # need a year of history for the lookbacks
    print(f"[{label}] {px.index[0].date()} .. {px.index[-1].date()}, {len(me) - 1} monthly decisions")
    cash = px["C"]
    print(f"cash leg CAGR {((cash.iloc[-1] / cash.iloc[0]) ** (365.25 / (cash.index[-1] - cash.index[0]).days) - 1) * 100:.2f}%/yr")
    a = argparse.Namespace(seeds=seeds)
    n = len(me) - 1
    N = np.tile([1.0, 0.0, 0.0], (n, 1))
    G = np.tile([0.0, 1.0, 0.0], (n, 1))
    H = np.tile([0.5, 0.5, 0.0], (n, 1))
    print("\n--- baselines ---")
    for name, w in (("equity buy&hold", N), ("gold buy&hold", G), ("50/50 equity/gold", H)):
        print(line(name, run(px, w, me)))
    p = px.to_numpy()
    descriptive(px, me)
    print("\n--- A. dual momentum ---")
    for L in (6, 9, 12):
        wts = []
        for i in range(n):
            t = me[i]
            rn, rg = p[t, 0] / p[t - 21 * L, 0] - 1, p[t, 1] / p[t - 21 * L, 1] - 1
            best, r_ = (0, rn) if rn >= rg else (1, rg)
            w = np.array([0.0, 0.0, 1.0]) if r_ <= 0 else (np.array([1.0, 0.0, 0.0]) if best == 0 else np.array([0.0, 1.0, 0.0]))
            wts.append(w)
        wts = np.array(wts)
        pf, pc = rotation_p(px, wts, me, a.seeds)
        print(line(f"dual momentum L={L}m", run(px, wts, me)) + f"; p(final wealth)={pf:.3f}, p(Calmar)={pc:.3f}; "
              f"time in N/G/cash {wts.mean(0).round(2).tolist()}")
    print("\n--- B. per-asset trend gate (50/50 of NIFTYBEES/GOLDBEES, off half in cash) ---")
    for L in (100, 150, 200):
        sma = px.rolling(L).mean().to_numpy()
        wts = np.array([[0.5 * (p[me[i], 0] > sma[me[i], 0]), 0.5 * (p[me[i], 1] > sma[me[i], 1]), 0.0] for i in range(n)])
        wts[:, 2] = 1 - wts[:, 0] - wts[:, 1]
        pf, pc = rotation_p(px, wts, me, a.seeds)
        print(line(f"trend gate SMA{L}", run(px, wts, me)) + f"; p(final wealth)={pf:.3f}, p(Calmar)={pc:.3f}; "
              f"time in N/G/cash {wts.mean(0).round(2).tolist()}")


if __name__ == "__main__":
    main()
