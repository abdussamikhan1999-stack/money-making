"""
Eightieth entry: a second batch of published approaches associated with top macro / stat-arb / vol-selling funds,
on survivorship-free instruments. Pre-registered here BEFORE running:

  1. CURRENCY CARRY (the classic macro-fund trade; Koijen-Moskowitz-Pedersen-Vrugt "Carry"). 9 currencies vs USD
     (EUR GBP JPY AUD NZD CAD CHF NOK SEK), monthly. carry_i = 3-month interbank rate_i - USD rate (FRED, LAGGED one month
     so no publication look-ahead). Long the top 3 carry currencies, short the bottom 3, equal weight, dollar-neutral.
     Monthly return = mean over longs of (spot return + carry/12) - same over shorts. Fill = next close after month-end.
     0.03% per leg on turnover. Control: 3 random longs + 3 random shorts each month (exactly the same mechanics).
  2. SECTOR-ETF PAIRS (the stat-arb archetype; Gatev-Goetzmann-Rouwenhorst 2006). 9 SPDR sector ETFs (XLB XLE XLF XLI XLK
     XLP XLU XLV XLY), 1998-12+. Formation 252 trading days: normalised price paths, sum of squared distances; the 5 closest
     pairs trade the next 126 days: open when the normalised spread exceeds 2 formation-sigmas (short the rich leg, long the
     cheap one, $1 each, daily rebalanced), close when it crosses zero or at the window end; sequential non-overlapping windows;
     0.05% per leg per side (0.2% per round trip). Control: 5 RANDOM pairs each window, same rules.
  3. SHORT-VOLATILITY TERM STRUCTURE (the vol-seller's trade). Signal = ^VIX / ^VIX3M at close t below a threshold in
     {1.00, 0.90} (contango); the position is filled at the NEXT close and earns the FOLLOWING day's SVXY return (a two-day
     lag), else T-bill cash. Sharpe is in EXCESS of cash. SVXY changed from -1x to -0.5x on 2018-02-28: sub-samples reported. SVXY 2011-10+, ^VIX3M 2006+. Baselines:
     SVXY buy&hold, S&P 500 (SPY), cash. Control: exact enumeration of circular rotations (>= 60 days) of the signal.

Everything here is retail-inaccessible from India as specified (US-listed ETFs, FX and shorting); it is research on whether the
approaches work, not an implementation plan.
"""

import argparse
import functools
import io
import itertools
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf

FX = {"EUR": ("EURUSD=X", False, "DEM"), "GBP": ("GBPUSD=X", False, "GBM"), "JPY": ("USDJPY=X", True, "JPM"),
      "AUD": ("AUDUSD=X", False, "AUM"), "NZD": ("NZDUSD=X", False, "NZM"), "CAD": ("USDCAD=X", True, "CAM"),
      "CHF": ("USDCHF=X", True, "CHM"), "NOK": ("USDNOK=X", True, "NOM"), "SEK": ("USDSEK=X", True, "SEM")}
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]


@functools.lru_cache(maxsize=None)
def hist(t, start, col="Close"):
    for _ in range(3):
        d = yf.Ticker(t).history(start=start)
        if len(d) and col in d:
            break
    else:
        raise RuntimeError(f"{t}: no data after 3 tries")
    x = d[col].dropna()
    x.index = pd.DatetimeIndex(x.index.date)
    return x[~x.index.duplicated()]


@functools.lru_cache(maxsize=None)
def fred(series):
    raw = urllib.request.urlopen(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=30).read()
    s = pd.read_csv(io.BytesIO(raw), index_col=0, parse_dates=True).iloc[:, 0]
    return pd.to_numeric(s, errors="coerce").dropna()


def month_end_rows(idx):
    ym = idx.to_period("M")
    return np.flatnonzero(ym != pd.Series(ym).shift(-1).to_numpy())[:-1]


def stats(r, ann=12, c=None):
    """c: optional per-period cash return; Sharpe is then in excess of cash (for strategies that hold cash)."""
    r = np.asarray(r)
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    yrs = len(r) / ann
    cagr = eq[-1] ** (1 / yrs) - 1
    dd = (1 - eq / peak).max()
    sd = r.std(ddof=1)
    ex = r - (np.asarray(c) if c is not None else 0.0)
    sharpe = ex.mean() / ex.std(ddof=1) * np.sqrt(ann) if ex.std(ddof=1) > 0 else float("nan")
    return dict(cagr=cagr * 100, vol=sd * np.sqrt(ann) * 100, sharpe=sharpe,
                dd=dd * 100, worst=r.min() * 100, final=eq[-1])


def line(name, r, ann=12, c=None):
    s = stats(r, ann, c)
    return (f"{name:30s} {s['cagr']:6.2f}%/yr, vol {s['vol']:5.1f}%, Sharpe {s['sharpe']:5.2f}, maxDD {s['dd']:5.1f}%, "
            f"worst period {s['worst']:6.2f}%")


# ------------------------------------------------------------------------------------------- 1. currency carry
def carry(draws):
    start = "2003-06-01"
    usd_rate = fred("IR3TIB01USM156N")
    spot, rate = {}, {}
    for c, (tkr, inverted, code) in FX.items():
        px = hist(tkr, start)
        spot[c] = 1 / px if inverted else px           # USD value of one unit of foreign currency
        rate[c] = fred(f"IR3TIB01{code}156N")
    S = pd.DataFrame(spot).dropna(how="all")
    S = S.ffill(limit=5)
    me = month_end_rows(S.index)
    me = me[me >= 20]
    p = S.to_numpy()
    n = len(me) - 1
    R = np.full((n, len(FX)), np.nan)      # spot return: close after month-end i -> close after month-end i+1
    K = np.full((n, len(FX)), np.nan)      # carry per month, from LAGGED rates (previous calendar month's value)
    for i in range(n):
        a, b = me[i] + 1, me[i + 1] + 1
        if b >= len(p):
            n = i
            break
        R[i] = p[b] / p[a] - 1
        dt = S.index[me[i]]
        prev_month = (dt.to_period("M") - 1).to_timestamp()
        for j, c in enumerate(FX):
            try:
                rs, us = rate[c][:prev_month], usd_rate[:prev_month]
                if (prev_month - rs.index[-1]).days > 65 or (prev_month - us.index[-1]).days > 65:
                    continue  # stale: the series ended (e.g. FRED's GBP series stops 2026-01); never reuse an old rate
                K[i, j] = (rs.iloc[-1] - us.iloc[-1]) / 100 / 12
            except IndexError:
                pass
    R, K = R[:n], K[:n]
    dates = S.index[me[:n]]
    tot = np.nan_to_num(R + K)   # a currency that does not exist yet has weight 0; 0*NaN would otherwise poison the sum
    Rz, Kz = np.nan_to_num(R), np.nan_to_num(K)
    print(f"CARRY: {len(FX)} currencies, {dates[0].date()} .. {dates[-1].date()}, {n} months")

    def pick(i, rng=None):
        ok = np.flatnonzero(np.isfinite(K[i]) & np.isfinite(R[i]))
        if len(ok) < 6:
            return None, None
        if rng is None:
            o = ok[np.argsort(K[i][ok])]
            return o[-3:], o[:3]
        s = rng.permutation(ok)
        return s[:3], s[3:6]

    def run(rng=None, comp=False, cost=0.0003):
        out, prev = [], np.zeros(len(FX))
        spot_c, carry_c = [], []
        for i in range(n):
            L, Sh = pick(i, rng)
            if L is None:
                continue
            w = np.zeros(len(FX))
            w[L], w[Sh] = 1 / 3, -1 / 3
            out.append(float((w * tot[i]).sum()) - cost * np.abs(w - prev).sum())
            spot_c.append(float((w * Rz[i]).sum()))
            carry_c.append(float((w * Kz[i]).sum()))
            prev = w
        return (np.array(out), np.array(spot_c), np.array(carry_c)) if comp else np.array(out)

    r, spot_c, carry_c = run(comp=True)
    print(line("carry L3/S3 dollar-neutral", r))
    print(f"  decomposition: carry income {carry_c.mean() * 12 * 100:+.2f}%/yr, spot P&L {spot_c.mean() * 12 * 100:+.2f}%/yr; "
          f"skew {pd.Series(r).skew():+.2f}; months < -3%: {int((r < -0.03).sum())}")
    yr = pd.Series(r, index=dates[-len(r):]).groupby(lambda d: d.year).apply(lambda x: (1 + x).prod() - 1)
    print("  worst calendar years:", ", ".join(f"{y}: {v * 100:+.1f}%" for y, v in yr.nsmallest(4).items()),
          "| best:", ", ".join(f"{y}: {v * 100:+.1f}%" for y, v in yr.nlargest(2).items()))
    # The random control redraws its whole book each month and so trades several times more than the persistent carry
    # ranking (review finding); a NET-vs-NET p would favour the strategy. Primary p is therefore GROSS of cost on both sides.
    rg = run(cost=0.0)
    ag = stats(rg)
    rng = np.random.default_rng(0)
    shg, fwg = [], []
    for _ in range(draws):
        s_ = stats(run(rng, cost=0.0))
        shg.append(s_["sharpe"])
        fwg.append(s_["final"])
    print(f"  GROSS of cost: strategy Sharpe {ag['sharpe']:.2f}; vs random 3v3 ({draws} draws) p(Sharpe)={((np.array(shg) >= ag['sharpe']).sum() + 1) / (draws + 1):.4f}, "
          f"p(wealth)={((np.array(fwg) >= ag['final']).sum() + 1) / (draws + 1):.4f}; random mean Sharpe {np.mean(shg):+.2f}")
    h = len(r) // 2
    print(f"  halves: Sharpe {stats(r[:h])['sharpe']:.2f} / {stats(r[h:])['sharpe']:.2f}; return {stats(r[:h])['cagr']:.1f}% / {stats(r[h:])['cagr']:.1f}%")


# ------------------------------------------------------------------------------------------- 2. sector pairs
def pairs(draws):
    px = pd.DataFrame({t: hist(t, "1998-12-01") for t in SECTORS}).dropna()
    P = px.to_numpy()
    ret = px.pct_change().to_numpy()
    F, T = 252, 126
    combos = list(itertools.combinations(range(len(SECTORS)), 2))
    starts = list(range(F, len(px) - T, T))
    print(f"\nPAIRS: {len(SECTORS)} sector ETFs, {px.index[starts[0]].date()} .. {px.index[min(starts[-1] + T, len(px) - 1)].date()}, "
          f"{len(starts)} windows x 5 pairs of {len(combos)}")

    def pair_window(a, b, s0):
        """daily P&L series over the trading window for pair (a,b), formation ending at s0."""
        base = P[s0 - F : s0 + T]
        norm = base / base[0]
        spread = norm[:, a] - norm[:, b]
        sig = spread[:F].std()
        pnl = np.zeros(T)
        pos = 0
        for d in range(T):
            t = F + d                    # index within `base` of day d's close; the return earned is close t-1 -> close t
            sig_t = spread[t - 2]        # signal at close t-2 -> fill at close t-1 -> earns day t (lag-1, no same-bar fill)
            if pos != 0 and ((pos == -1 and sig_t <= 0) or (pos == 1 and sig_t >= 0)):
                pnl[d] -= 0.0010         # spread re-crossed zero: close (2 legs x 0.05%)
                pos = 0
            if pos == 0 and abs(sig_t) > 2 * sig:
                pos = -1 if sig_t > 0 else 1   # spread>0: leg a rich -> short a, long b
                pnl[d] -= 0.0010         # open (2 legs x 0.05%)
            if pos != 0:
                r = ret[s0 + d]
                pnl[d] += pos * (r[a] - r[b])
                if d == T - 1:
                    pnl[d] -= 0.0010     # forced close at the window end
                    pos = 0
        return pnl

    allp = {}  # (window index, pair) -> pnl series
    ssd_pick = []
    for wi, s0 in enumerate(starts):
        base = P[s0 - F : s0]
        norm = base / base[0]
        ssd = [((norm[:, a] - norm[:, b]) ** 2).sum() for a, b in combos]
        ssd_pick.append(np.argsort(ssd)[:5])
        for ci, (a, b) in enumerate(combos):
            allp[(wi, ci)] = pair_window(a, b, s0)

    def series(choice):
        out = []
        for wi in range(len(starts)):
            out.append(np.mean([allp[(wi, ci)] for ci in choice[wi]], axis=0))
        return np.concatenate(out)

    r = series(ssd_pick)
    s = stats(r, 252)
    print(line("SSD-selected top-5 pairs", r, 252))
    hs = np.array_split(r, 4)
    print(f"  quarters (total P&L per $1 pair capital): {[f'{x.sum() * 100:+.1f}%' for x in hs]}; halves Sharpe "
          f"{stats(r[: len(r) // 2], 252)['sharpe']:.2f}/{stats(r[len(r) // 2:], 252)['sharpe']:.2f}")
    rng = np.random.default_rng(0)
    sh, tot = [], []
    for _ in range(draws):
        ch = [rng.choice(len(combos), 5, replace=False) for _ in starts]
        rr = series(ch)
        sh.append(stats(rr, 252)["sharpe"])
        tot.append(rr.sum())
    print(f"  vs 5 RANDOM pairs per window ({draws} draws): p(Sharpe)={((np.array(sh) >= s['sharpe']).sum() + 1) / (draws + 1):.4f}, "
          f"p(total P&L)={((np.array(tot) >= r.sum()).sum() + 1) / (draws + 1):.4f}; random mean Sharpe {np.mean(sh):+.2f}, "
          f"mean total {np.mean(tot) * 100:+.1f}% vs {r.sum() * 100:+.1f}%")


# ------------------------------------------------------------------------------------------- 3. short-vol term structure
def shortvol():
    start = "2006-06-01"
    svxy, spy = hist("SVXY", start), hist("SPY", start)
    vix, v3 = hist("^VIX", start), hist("^VIX3M", start)
    df = pd.DataFrame({"svxy": svxy, "spy": spy, "vix": vix, "v3": v3}).dropna()
    y = hist("^IRX", start).reindex(df.index).ffill().bfill() / 100
    cash = (1 + y).pow(1 / 252) - 1
    ret = df[["svxy", "spy"]].pct_change()
    ratio = df["vix"] / df["v3"]
    print(f"\nSHORT-VOL: SVXY, {df.index[0].date()} .. {df.index[-1].date()}, {len(df)} days")
    R = ret["svxy"].to_numpy()
    C = cash.to_numpy()
    valid = np.arange(len(df)) >= 2
    post = np.asarray(df.index >= pd.Timestamp("2018-03-01"))   # SVXY became -0.5x on 2018-02-28
    cv = C[valid]
    print(line("SVXY buy&hold", R[valid], 252, cv))
    print(line("SPY buy&hold", ret["spy"].to_numpy()[valid], 252, cv))
    print(line("SVXY buy&hold, -0.5x era only", R[post], 252, C[post]))
    for thr in (1.0, 0.9):
        sig = (ratio.to_numpy() < thr)                         # known at close t
        w = np.zeros(len(df))
        w[2:] = sig[:-2]                                        # signal at close t-2 -> fill at close t-1 -> earns day t
        full = np.where(w > 0, R, C)
        r = full[valid]
        a = stats(r, 252, cv)
        ks = range(60, len(r) - 60)
        shs = [stats(np.where(np.roll(w, k) > 0, R, C)[valid], 252, cv)["sharpe"] for k in ks]   # exact rotation enumeration
        p = ((np.array(shs) >= a["sharpe"]).sum() + 1) / (len(shs) + 1)
        worst = int(np.nanargmin(R))
        print(line(f"SVXY when VIX/VIX3M<{thr}", r, 252, cv) + f"; in market {w[valid].mean():.0%}; exact rotation p(Sharpe)={p:.4f}; "
              f"held into SVXY's worst day {df.index[worst].date()} ({R[worst] * 100:+.0f}%): {'YES' if w[worst] > 0 else 'no'}")
        print(line(f"   same rule, -0.5x era only", full[post], 252, C[post]) + f"; in market {w[post].mean():.0%}")
        yr = pd.Series(full, index=df.index).groupby(lambda d: d.year).apply(lambda x: (1 + x).prod() - 1)
        print("   worst years:", ", ".join(f"{k}: {v * 100:+.0f}%" for k, v in yr.nsmallest(3).items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=3000)
    ap.add_argument("--only", choices=("carry", "pairs", "shortvol"))
    a = ap.parse_args()
    if a.only in (None, "carry"):
        carry(a.draws)
    if a.only in (None, "pairs"):
        pairs(a.draws)
    if a.only in (None, "shortvol"):
        shortvol()


if __name__ == "__main__":
    main()
