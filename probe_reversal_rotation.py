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

# Sixty-eighth entry: 52 DIFFERENT NSE large/mid-caps (zero overlap with WIDE_UNIVERSE) for a
# stock-independent replication. Today's constituents, so the same survivorship caveat applies.
UNIVERSE_B = ["AMBUJACEM", "BANKBARODA", "BEL", "BOSCHLTD", "CANBK", "CHOLAFIN", "COLPAL", "DABUR", "DLF", "GAIL",
              "GODREJCP", "HAVELLS", "ICICIGI", "INDIGO", "IOC", "JINDALSTEL", "LICHSGFIN", "MARICO", "MOTHERSON",
              "NHPC", "PFC", "PIDILITIND", "PNB", "RECLTD", "SAIL", "SIEMENS", "SRF", "TATAPOWER", "TORNTPHARM",
              "TVSMOTOR", "VOLTAS", "BHEL", "ABB", "APOLLOHOSP", "AUROPHARMA", "BANDHANBNK", "BERGEPAINT", "BIOCON",
              "CUMMINSIND", "ESCORTS", "FEDERALBNK", "GLENMARK", "IDFCFIRSTB", "LUPIN", "MUTHOOTFIN", "PAGEIND",
              "PETRONET", "TATACOMM", "INDUSINDBK", "M&M", "ADANIENT", "NMDC", "IGL", "SHREECEM"]
SYMBOLS = None  # set by --universe-b
US_UNIVERSE = ["AAPL", "MSFT", "AMZN", "JPM", "JNJ", "XOM", "WMT", "PG", "KO", "PEP", "CVX", "MRK", "PFE", "CSCO", "INTC",
               "ORCL", "IBM", "T", "VZ", "HD", "MCD", "DIS", "BA", "CAT", "MMM", "GE", "HON", "UNH", "ABT", "AMGN", "GILD",
               "BMY", "LLY", "TXN", "QCOM", "ADBE", "COST", "NKE", "SBUX", "LOW", "TGT", "UPS", "FDX", "DE", "MDT", "AXP",
               "GS", "MS", "C", "BAC", "WFC", "USB"]
CACHE = os.environ.get("REV_CACHE", "")


def load_matrices(period="10y", stress=False):
    """stress=True: add the Fortieth entry's 4 real blowups (survivorship stress)."""
    cache = ""
    if CACHE:  # key on period AND universe, so a 20y run can never read a 10y cache (review finding)
        root = os.path.splitext(CACHE)[0]
        cache = f"{root}_{period}{'_stress' if stress else ''}{'_B' if SYMBOLS else ''}.pkl"
    if cache and os.path.exists(cache):
        return pickle.load(open(cache, "rb"))
    if SYMBOLS:
        from data_yfinance import fetch_candles
        series = {}
        for sym in SYMBOLS:
            cds = []
            for _ in range(3):
                cds = fetch_candles(sym + ".NS", "1d", period)
                if cds:
                    break
            if cds:
                series[sym + ".NS"] = cds
            else:
                print(f"{sym}: fetch failed, excluded")
    elif stress:
        from probe_ibs_rotation_survivorship import build_stress_price_series
        series = build_stress_price_series(period)
    else:
        series = build_wide_price_series(period)
    cal_candles = fetch_calendar(period)
    cal = [c["date"].date() for c in cal_candles]
    idx = pd.DatetimeIndex(cal)
    out = {}
    for name in ("close", "high", "low"):
        out[name] = pd.DataFrame(
            {s: pd.Series([c[name] for c in cds], index=pd.DatetimeIndex([c["date"].date() for c in cds]))
             for s, cds in series.items()}).reindex(idx).ffill()
    out["me"] = [idx.get_loc(pd.Timestamp(d)) for d in month_end_dates(cal_candles)]
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
    if kind == "skew":  # Amaya-Christoffersen-Jacobs-Vasquez 2015: realized skewness of daily returns over
        # `window` days predicts returns NEGATIVELY (low/negative-skew names outperform), so ascending sort
        # (lowest score first) already picks the low-skew names, same convention as "rev" below, no negation.
        return c.pct_change().rolling(window, min_periods=window).skew()
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


def compute_atr(M, period=14):
    """Simple (unsmoothed) ATR per stock, vectorized — same convention as
    indicators.average_true_range (plain rolling mean of true range, not
    Wilder's exponential smoothing), reused here instead of a second
    definition. ATR[t] uses only high/low/close through day t, so using
    atr[a] (the ranking date) to size a stop/target for a trade entered at
    a+lag is no-lookahead, the same convention every other indicator in
    this project's daily strategies already follows."""
    h, l, c = M["high"], M["low"], M["close"]
    prev_close = c.shift(1)
    tr = np.maximum(h - l, np.maximum((h - prev_close).abs(), (l - prev_close).abs()))
    return tr.rolling(period, min_periods=period).mean()


def simulate(M, S, top_k, lag, rng=None, capital=100_000.0, hs=None, hs_mult=1.0, gate=None, hold=None,
             atr=None, stop_mult=None, target_mult=None):
    """Long the top_k lowest-score names (random eligible names if rng given).
    hs: optional half-spread matrix (see corwin_schultz_half_spread); each pick pays
    ITS OWN half spread x hs_mult on both legs, so the asymmetry between
    the strategy's picks and random picks is priced in, not assumed.

    atr/stop_mult/target_mult (all default None, old behavior unchanged): optional
    ATR-based stop-loss and take-profit, checked day-by-day against that pick's
    OWN ATR as of the ranking date (no lookahead — see compute_atr). If a day's
    low breaches the stop AND its high clears the target, the stop takes priority
    (this project's established adverse-first-on-a-tie convention, matching
    backtest_daily.py's own day-path approximation). A pick that never triggers
    either exits at the scheduled month-end/hold-day close exactly as before."""
    c = M["close"].to_numpy()
    use_stops = atr is not None and stop_mult is not None and target_mult is not None
    h = M["high"].to_numpy() if use_stops else None
    l = M["low"].to_numpy() if use_stops else None
    atrv = atr.to_numpy() if use_stops else None
    sc = S.to_numpy()
    hsv = hs.to_numpy() if hs is not None else None
    me = M["me"]
    cap, peak, dd = capital, capital, 0.0
    months, at, spreads = [], [], []
    for a, b in zip(me[:-1], me[1:]):
        ea, eb = a + lag, b + lag
        if hold is not None:  # exit `hold` trading days after entry instead of at the next month-end; cash until then
            eb = min(ea + hold, b + lag)
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
            entry_px, exit_idx, exit_px = c[ea, j], eb, c[eb, j]
            if use_stops and not np.isnan(atrv[a, j]):
                stop_px = entry_px - stop_mult * atrv[a, j]
                target_px = entry_px + target_mult * atrv[a, j]
                for d in range(ea + 1, eb + 1):
                    if np.isnan(l[d, j]) or np.isnan(h[d, j]):
                        continue
                    stopped, hit_target = l[d, j] <= stop_px, h[d, j] >= target_px
                    if stopped:
                        exit_idx, exit_px = d, stop_px
                        break
                    if hit_target:
                        exit_idx, exit_px = d, target_px
                        break
            if hsv is not None:
                r = exit_px * (1 - hs_mult * np.nan_to_num(hsv[exit_idx, j])) / (entry_px * (1 + hs_mult * np.nan_to_num(hsv[ea, j]))) - 1
                spreads.append(np.nan_to_num(hsv[ea, j]))
            else:
                r = exit_px / entry_px - 1
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
    nifty_full = pm.load()["nifty"].dropna()  # SMA is computed on the FULL history, so no warm-up falls inside the backtest
    align = lambda x: x.reindex(M["close"].index.union(x.index)).ffill().reindex(M["close"].index)
    nifty = align(nifty_full)
    S = scores(M, "ibs", 5)
    for top_k in (5, 8):
        base = simulate(M, S, top_k, 1)
        print(f"\ntop_k={top_k} ungated: {cagr(base['final'], years):.2f}%/yr, maxDD {base['max_dd']:.1%}")
        for L in (100, 150, 200):
            sma = align(nifty_full.rolling(L, min_periods=L).mean())
            gate = np.where(sma.isna(), True, nifty > sma)  # (nifty > NaN) is False, so fillna would never fire
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
            flip_cost = lambda mask: np.where(np.abs(np.diff(np.concatenate([[1], mask.astype(int)]))) > 0, 0.002, 0.0)
            g = np.where(on, rets, 0.0) - flip_cost(on)
            gf, gdd = stats(g)
            rng = np.random.default_rng(0)
            n_off = int((~on).sum())
            fin, dds = [], []
            for _ in range(seeds):
                m = np.ones(len(rets), bool)
                m[rng.choice(len(rets), n_off, replace=False)] = False
                f_, d_ = stats(np.where(m, rets, 0.0) - flip_cost(m))  # control pays the same switching cost
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
        Mj["me"] = [m - j for m in M["me"] if m - j >= 0]
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


def load_us(period_start="2006-06-01"):
    """Seventy-fourth entry: 52 current S&P-100-type US large caps on the S&P 500 trading calendar."""
    import yfinance as yf
    cal = yf.Ticker("^GSPC").history(start=period_start)["Close"].dropna()
    idx = pd.DatetimeIndex(cal.index.date)
    out = {}
    raw = {}
    for t in US_UNIVERSE:
        for _ in range(3):
            d = yf.Ticker(t).history(start=period_start)
            if len(d):
                break
        raw[t] = d
        if not len(d):
            print(f"{t}: fetch failed after 3 retries, excluded")
    for name in ("Close", "High", "Low"):
        out[name.lower()] = pd.DataFrame({t: pd.Series(d[name].to_numpy(), index=pd.DatetimeIndex(d.index.date))
                                          for t, d in raw.items() if len(d)}).reindex(idx).ffill()
    ym = idx.to_period("M")
    out["me"] = [int(i) for i in np.flatnonzero(ym != pd.Series(ym).shift(-1).to_numpy())][:-1]
    return out


def us_test(seeds, cost=0.05):
    """Same pre-declared month-end tests on US large caps: horizon curve j=0 vs j=9, hold 5/10/21 (top_k=5,
    lag 1, US cost 0.05%/leg and no DP charge), both decades, same-hold random control."""
    global COST_PCT, DP
    COST_PCT, DP = cost, 0.0
    M = load_us()
    print(f"US universe {M['close'].shape[1]} stocks, {M['close'].index[0].date()} .. {M['close'].index[-1].date()}, {len(M['me'])} month-ends")
    S = scores(M, "ibs", 5)
    cut = M["close"].index.searchsorted(pd.Timestamp("2016-09-19"))
    horizon_curve(M, js=(0, 9), hi=cut)
    print("---- later decade ----")
    horizon_curve(M, js=(0, 9), lo=cut)
    for label, lo, hi in (("2007-16", 0, cut), ("2016-26", cut, len(M["close"]))):
        Mx = dict(M)
        Mx["me"] = [m for m in M["me"] if lo <= m and m + 30 < hi]
        row = []
        for h in (5, 10, 21):
            r = simulate(Mx, S, 5, 1, hold=h)
            yrs = len(r["months"]) / 12
            rng = np.random.default_rng(0)
            fin = np.array([simulate(Mx, S, 5, 1, rng=rng, hold=h)["final"] for _ in range(seeds)])
            p = ((fin >= r["final"]).sum() + 1) / (seeds + 1)
            row.append(f"hold {h:2d}d {cagr(r['final'], yrs):6.2f}%/yr (DD {r['max_dd']:.0%}, rnd {cagr(fin.mean(), yrs):5.2f}%, p={p:.4f})")
        print(f"US {label}: " + " | ".join(row))


def oos_horizon(split="2016-09-19"):
    """Sixty-sixth entry: the horizon-curve hypothesis (edge accrues in the ~10 trading days after a
    month-end entry) scored on the decade BEFORE the one it was found in, and on the later decade."""
    M = load_matrices("20y")
    cut = M["close"].index.searchsorted(pd.Timestamp(split))
    print(f"EARLIER (out-of-sample) {M['close'].index[0].date()} .. {split}")
    horizon_curve(M, hi=cut)
    print(f"\nLATER (where it was found) {split} .. {M['close'].index[-1].date()}")
    horizon_curve(M, lo=cut)


def oos_anchor_test(period="20y", split="2016-09-19"):
    """Independent-in-time test of the Sixty-fourth entry's month-end hypothesis: the window
    (last 5 trading days, j=0..4) was defined on 2016-2026, so score it on the EARLIER decade.
    Survivorship is worse in the early years (today's constituents) but it lifts every phase alike;
    the tested quantity is the phase DIFFERENCE, plus a random-portfolio phase control."""
    M = load_matrices(period)
    S = scores(M, "ibs", 5)
    cut = M["close"].index.searchsorted(pd.Timestamp(split))
    print(f"universe {M['close'].shape[1]} stocks, {M['close'].index[0].date()} .. {M['close'].index[-1].date()}, split at {split}")
    print(f"stocks with a price by 2008: {int(M['close'].iloc[250:300].notna().any().sum())}")

    def stream(j, lo, hi, rng=None):
        """{month-end row: return}, keyed by the CALENDAR month so anchors align (review finding: positional
        alignment paired different months when the window edge cut some anchors' first month)."""
        Mj = dict(M)
        Mj["me"] = [m - j for m in M["me"] if lo <= m - j < hi]
        r = simulate(Mj, S, 5, 1, rng=rng)
        return {a + j: x for a, x in zip(r["at"], r["months"])}

    windows = {"last-5-days (j=0-4)": list(range(0, 5)), "mid/late (j=7-9,15-18)": [7, 8, 9, 15, 16, 17, 18]}
    for label, lo, hi in (("EARLIER (out-of-sample) ", 0, cut), ("LATER (where it was found)", cut, len(M["close"]))):
        streams = {name: [stream(j, lo, hi) for j in js] for name, js in windows.items()}
        common = sorted(set.intersection(*[set(d) for ds in streams.values() for d in ds]))
        n = len(common)
        A = np.mean([[d[m] for m in common] for d in streams["last-5-days (j=0-4)"]], axis=0)
        B = np.mean([[d[m] for m in common] for d in streams["mid/late (j=7-9,15-18)"]], axis=0)
        d = A - B
        rng = np.random.default_rng(3)
        rmean = lambda js: np.mean([np.mean([np.mean([stream(j, lo, hi, rng).get(m, np.nan) for m in common]) for _ in range(100)]) for j in js])
        ra, rb = rmean(windows["last-5-days (j=0-4)"]), rmean(windows["mid/late (j=7-9,15-18)"])
        print(f"{label}: {n} months | IBS last-5-days {A.mean():+.2%}/mo vs mid/late {B.mean():+.2%}/mo, diff {d.mean():+.2%}, "
              f"paired t={d.mean() / (d.std(ddof=1) / np.sqrt(n)):.2f}, A>B in {(A > B).mean():.0%} of months | "
              f"random portfolios {ra:+.2%} vs {rb:+.2%}")
        qs = [f"{a.mean() - b.mean():+.2%}" for a, b in zip(np.array_split(A, 4), np.array_split(B, 4))]
        print(f"    quarter-by-quarter diff: {qs}")


def horizon_curve(M, top_k=5, js=(0, 9), lo=0, hi=10**9):
    """Where in the month does the IBS edge accrue? For each month, enter the top_k IBS picks at the
    lag-1 close after the ranking date (j trading days before month-end) and record the GROSS mean
    return over h trading days for h=1..25, minus the equal-weight return of every eligible stock over
    the same days (the universe baseline). Gross on purpose: this is a shape, not a P&L."""
    S = scores(M, "ibs", 5)
    c, sc = M["close"].to_numpy(), S.to_numpy()
    hs = (1, 2, 3, 5, 8, 10, 13, 16, 21, 25)
    for j in js:
        rows = {h: [] for h in hs}
        for m in M["me"]:
            a = m - j
            if a < max(lo, 0) or a >= hi or a + 1 + max(hs) >= len(c):
                continue
            ok = ~np.isnan(sc[a]) & ~np.isnan(c[a + 1])
            cand = np.flatnonzero(ok)
            if len(cand) < top_k:
                continue
            pick = cand[np.argsort(sc[a][cand])[:top_k]]
            for h in hs:
                r = c[a + 1 + h] / c[a + 1] - 1
                good = cand[~np.isnan(r[cand])]
                pk = [i for i in pick if not np.isnan(r[i])]
                if len(pk) == top_k and len(good) > top_k:
                    rows[h].append(r[pick].mean() - r[good].mean())
        print(f"\nj={j} ({'month-end entry' if j == 0 else 'mid-month entry, ~9 trading days before month-end'}): "
              f"IBS picks minus equal-weight universe, gross, by holding horizon h (trading days)")
        prev = 0.0
        for h in hs:
            x = np.array(rows[h])
            t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
            half = len(x) // 2
            print(f"  h={h:2d}: excess {x.mean():+.2%} (t={t:5.2f}, n={len(x)}), halves {x[:half].mean():+.2%}/{x[half:].mean():+.2%}, "
                  f"increment since previous h {x.mean() - prev:+.2%}")
            prev = x.mean()


def hold_test(M, years, seeds, stress=False):
    """Post-hoc policy suggested by the Sixty-sixth entry's horizon curve: hold h days from the month-end
    entry, then cash. Same costs, same lag, random control uses the identical hold."""
    S = scores(M, "ibs", 5)
    for top_k in (5, 8):
        print(f"\ntop_k={top_k}, IBS(5), lag 1, month-end entry, exit after h trading days (cash otherwise, 0%):")
        for h in (5, 8, 10, 13, 21):
            r = simulate(M, S, top_k, 1, hold=h)
            rng = np.random.default_rng(0)
            finals = np.array([simulate(M, S, top_k, 1, rng=rng, hold=h)["final"] for _ in range(seeds)])
            p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
            mo = r["months"]
            hh = len(mo) // 2
            qs = [np.prod(1 + q) - 1 for q in np.array_split(mo, 4)]
            cal = cagr(r["final"], years) / (100 * r["max_dd"]) if r["max_dd"] else float("nan")
            print(f"  hold {h:2d}d: {cagr(r['final'], years):6.2f}%/yr, maxDD {r['max_dd']:.1%}, Calmar {cal:.2f}, halves "
                  f"{np.prod(1 + mo[:hh]) - 1:+.0%}/{np.prod(1 + mo[hh:]) - 1:+.0%}, quarters {[f'{q:+.0%}' for q in qs]}, "
                  f"random(same hold) {cagr(finals.mean(), years):.2f}%/yr, p={p:.4f}")


def atr_stop_test(M, years, seeds, top_k=5):
    """IBS(5) rotation, lag 1, with an ATR-based stop-loss/take-profit checked
    day-by-day during each month's hold instead of exiting only at the
    scheduled month-end close. Pre-registered grid: stop_mult in {1.0, 1.5,
    2.0} x target_mult in {2.0, 3.0, 4.0} (the same R-multiple range this
    project's other ATR-stopped strategies use — e.g. ThreeBarBreakoutStrategy's
    default target_r_multiple=2.5, RSI-2's stop_atr_multiple=3.0). Baseline
    (no stop/target) is the family's own established lag-1 number, ~20%/yr
    (Fifty-ninth entry)."""
    S = scores(M, "ibs", 5)
    atr = compute_atr(M)
    base = simulate(M, S, top_k, 1)
    print(f"IBS(5) top_k={top_k} lag=1, NO stop/target (baseline): {cagr(base['final'], years):.2f}%/yr, maxDD {base['max_dd']:.1%}")
    for stop_mult in (1.0, 1.5, 2.0):
        for target_mult in (2.0, 3.0, 4.0):
            r = simulate(M, S, top_k, 1, atr=atr, stop_mult=stop_mult, target_mult=target_mult)
            rng = np.random.default_rng(0)
            finals = np.array([simulate(M, S, top_k, 1, rng=rng, atr=atr, stop_mult=stop_mult,
                                         target_mult=target_mult)["final"] for _ in range(seeds)])
            p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
            mo = r["months"]
            half = len(mo) // 2
            qs = [np.prod(1 + q) - 1 for q in np.array_split(mo, 4)]
            print(f"  stop={stop_mult:.1f}xATR target={target_mult:.1f}xATR: {cagr(r['final'], years):6.2f}%/yr, "
                  f"maxDD {r['max_dd']:.1%}, halves {np.prod(1 + mo[:half]) - 1:+.0%}/{np.prod(1 + mo[half:]) - 1:+.0%}, "
                  f"quarters {[f'{q:+.0%}' for q in qs]}, random mean {cagr(finals.mean(), years):.2f}%/yr, p={p:.4f}")


def atr_stop_cell_detail(M, years, seeds, stop_mult, target_mult, top_k=5):
    """Annualized quarter-split (with its own random-control p per quarter) for one
    stop_mult/target_mult cell, since atr_stop_test's own quarters print total return
    per chunk only."""
    S = scores(M, "ibs", 5)
    atr = compute_atr(M)
    r = simulate(M, S, top_k, 1, atr=atr, stop_mult=stop_mult, target_mult=target_mult)
    mo = r["months"]
    n = len(mo)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n]
    print(f"IBS(5) top_k={top_k} lag=1, stop={stop_mult:.2f}xATR target={target_mult:.2f}xATR — quarter-split detail:")
    rng = np.random.default_rng(1)
    random_mo = [simulate(M, S, top_k, 1, rng=rng, atr=atr, stop_mult=stop_mult,
                           target_mult=target_mult)["months"] for _ in range(min(seeds, 300))]
    for i in range(4):
        lo, hi = cuts[i], cuts[i + 1]
        ch = mo[lo:hi]
        yrs_q = len(ch) / 12
        total = np.prod(1 + ch) - 1
        ann = ((1 + total) ** (1 / yrs_q) - 1) * 100 if yrs_q > 0 and total > -1 else float("nan")
        rand_totals = np.array([np.prod(1 + rm[lo:hi]) - 1 for rm in random_mo if len(rm) >= hi])
        p_q = ((rand_totals >= total).sum() + 1) / (len(rand_totals) + 1) if len(rand_totals) else float("nan")
        print(f"  Q{i + 1} ({len(ch)} months, ~{yrs_q:.1f}y): total {total:+.1%}, annualized {ann:+.2f}%/yr, "
              f"random-control mean {rand_totals.mean():+.1%}, p={p_q:.4f}")


def atr_stop_perturb(M, years, seeds, top_k=5):
    """Perturbation sweep around the one cell (stop=1.0xATR, target=4.0xATR) that
    beat the no-stop baseline's Calmar in atr_stop_test — a neighborhood grid, the
    same Davey-style robustness check used throughout this file (smooth / no-cliff
    across nearby values = real; a peak only at the exact chosen point = a
    single-point-fit artifact, e.g. the Squeeze/regime-gate entries' own warnings)."""
    S = scores(M, "ibs", 5)
    atr = compute_atr(M)
    base = simulate(M, S, top_k, 1)
    print(f"baseline (no stop/target): {cagr(base['final'], years):.2f}%/yr, maxDD {base['max_dd']:.1%}, "
          f"Calmar {cagr(base['final'], years) / (100 * base['max_dd']):.2f}")
    print("perturbation grid around stop=1.0xATR, target=4.0xATR:")
    for stop_mult in (0.75, 1.0, 1.25, 1.5):
        row = []
        for target_mult in (3.0, 3.5, 4.0, 4.5, 5.0):
            r = simulate(M, S, top_k, 1, atr=atr, stop_mult=stop_mult, target_mult=target_mult)
            ann = cagr(r["final"], years)
            calmar = ann / (100 * r["max_dd"]) if r["max_dd"] else float("nan")
            mo = r["months"]
            half = len(mo) // 2
            h1, h2 = np.prod(1 + mo[:half]) - 1, np.prod(1 + mo[half:]) - 1
            consistent = (h1 > 0) == (h2 > 0)
            row.append(f"t={target_mult:.1f}: {ann:6.2f}%/yr DD{r['max_dd']:.0%} Calmar{calmar:.2f}"
                       f"{'' if consistent else ' INCONSISTENT'}")
        print(f"  stop={stop_mult:.2f}xATR: " + " | ".join(row))


def atr_target_widen(M, years, top_k=5):
    """Does the target_mult axis ever turn over, or does it just keep climbing toward
    the no-stop baseline as the target widens (i.e. the target stops doing any real
    work)? Widens target_mult from 4.0 out to 1000 (effectively "stop only, target
    never triggers") at three stop levels, holding everything else fixed."""
    S = scores(M, "ibs", 5)
    atr = compute_atr(M)
    base = simulate(M, S, top_k, 1)
    print(f"baseline (no stop/target): {cagr(base['final'], years):.2f}%/yr, maxDD {base['max_dd']:.1%}, "
          f"Calmar {cagr(base['final'], years) / (100 * base['max_dd']):.2f}")
    for stop_mult in (0.75, 1.0, 1.25):
        row = []
        prev_ann = None
        for target_mult in (4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 1000.0):
            r = simulate(M, S, top_k, 1, atr=atr, stop_mult=stop_mult, target_mult=target_mult)
            ann = cagr(r["final"], years)
            calmar = ann / (100 * r["max_dd"]) if r["max_dd"] else float("nan")
            mo = r["months"]
            half = len(mo) // 2
            h1, h2 = np.prod(1 + mo[:half]) - 1, np.prod(1 + mo[half:]) - 1
            consistent = (h1 > 0) == (h2 > 0)
            delta = f"({ann - prev_ann:+.2f})" if prev_ann is not None else ""
            prev_ann = ann
            label = "stop-only" if target_mult >= 1000 else f"t={target_mult:.0f}"
            row.append(f"{label}: {ann:6.2f}%/yr{delta} DD{r['max_dd']:.0%} Calmar{calmar:.2f}"
                       f"{'' if consistent else ' INCONSISTENT'}")
        print(f"  stop={stop_mult:.2f}xATR: " + " | ".join(row))


STOP_ONLY_TARGET = 1_000.0  # effectively disables the target leg (never triggers within a month)


def _cell_stats(M, S, atr, top_k, stop_mult, years, seeds, rng_seed=0):
    r = simulate(M, S, top_k, 1, atr=atr, stop_mult=stop_mult, target_mult=STOP_ONLY_TARGET)
    ann = cagr(r["final"], years)
    calmar = ann / (100 * r["max_dd"]) if r["max_dd"] else float("nan")
    mo = r["months"]
    half = len(mo) // 2
    h1, h2 = np.prod(1 + mo[:half]) - 1, np.prod(1 + mo[half:]) - 1
    consistent = (h1 > 0) == (h2 > 0)
    qs = [np.prod(1 + q) - 1 for q in np.array_split(mo, 4)]
    rng = np.random.default_rng(rng_seed)
    finals = np.array([simulate(M, S, top_k, 1, rng=rng, atr=atr, stop_mult=stop_mult,
                                 target_mult=STOP_ONLY_TARGET)["final"] for _ in range(seeds)])
    p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
    return dict(r=r, ann=ann, calmar=calmar, h1=h1, h2=h2, consistent=consistent, qs=qs,
                p=p, random_mean=cagr(finals.mean(), years))


def stop_only_full_rigor(top_k=5, seeds=1500):
    """Full-rigor pass on the ATR-STOP-ONLY variant (target effectively disabled —
    the Hundred-and-third follow-up found almost all of the Calmar gain over the
    no-stop baseline comes from the stop, not the target). Pre-registered grid:
    stop_mult in {0.5, 0.75, 1.0, 1.25, 1.5, 2.0}, top_k=5, IBS(5), lag=1, the
    family's own 52-stock WIDE_UNIVERSE. Checks applied, in this project's own
    established order: (1) walk-forward + 1,500-seed significance per cell on the
    base universe (screening); (2) quarter-split detail on the two best-by-Calmar
    cells; (3) survivorship stress (the Fortieth entry's 4 real blowups, 56 stocks)
    on those same two cells; (4) cross-universe replication (UNIVERSE_B, 54
    different NSE names) on those same two cells. A cell only counts as a real
    survivor if it clears ALL of these, not just the first screen."""
    global SYMBOLS
    print("=== (1) screening: stop_mult grid on the base 52-stock universe ===")
    M = load_matrices()
    years = (M["close"].index[M["me"][-1]] - M["close"].index[M["me"][0]]).days / 365.25
    S = scores(M, "ibs", 5)
    atr = compute_atr(M)
    base = simulate(M, S, top_k, 1)
    print(f"baseline (no stop): {cagr(base['final'], years):6.2f}%/yr, maxDD {base['max_dd']:.1%}, "
          f"Calmar {cagr(base['final'], years) / (100 * base['max_dd']):.2f}")
    grid = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
    cells = {}
    for stop_mult in grid:
        s = _cell_stats(M, S, atr, top_k, stop_mult, years, seeds)
        cells[stop_mult] = s
        print(f"  stop={stop_mult:.2f}xATR: {s['ann']:6.2f}%/yr, maxDD {s['r']['max_dd']:.1%}, Calmar {s['calmar']:.2f}, "
              f"halves {s['h1']:+.0%}/{s['h2']:+.0%} {'CONSISTENT' if s['consistent'] else 'INCONSISTENT'}, "
              f"quarters {[f'{q:+.0%}' for q in s['qs']]}, random mean {s['random_mean']:.2f}%/yr, p={s['p']:.4f}")

    best_two = sorted(grid, key=lambda sm: -cells[sm]["calmar"])[:2]
    print(f"\ntop 2 by Calmar: stop={best_two[0]:.2f}xATR (Calmar {cells[best_two[0]]['calmar']:.2f}), "
          f"stop={best_two[1]:.2f}xATR (Calmar {cells[best_two[1]]['calmar']:.2f})")

    print("\n=== (2) quarter-split detail (annualized, own random control per quarter) on the top 2 ===")
    for stop_mult in best_two:
        atr_stop_cell_detail(M, years, seeds, stop_mult, STOP_ONLY_TARGET, top_k)

    print("\n=== (3) survivorship stress (4 real blowups added, 56 stocks) on the top 2 ===")
    Ms = load_matrices(stress=True)
    years_s = (Ms["close"].index[Ms["me"][-1]] - Ms["close"].index[Ms["me"][0]]).days / 365.25
    Ss = scores(Ms, "ibs", 5)
    atrs = compute_atr(Ms)
    base_s = simulate(Ms, Ss, top_k, 1)
    print(f"stress-universe baseline (no stop): {cagr(base_s['final'], years_s):6.2f}%/yr, maxDD {base_s['max_dd']:.1%}")
    for stop_mult in best_two:
        s = _cell_stats(Ms, Ss, atrs, top_k, stop_mult, years_s, seeds)
        print(f"  stop={stop_mult:.2f}xATR (stress): {s['ann']:6.2f}%/yr, maxDD {s['r']['max_dd']:.1%}, Calmar {s['calmar']:.2f}, "
              f"halves {s['h1']:+.0%}/{s['h2']:+.0%} {'CONSISTENT' if s['consistent'] else 'INCONSISTENT'}, "
              f"quarters {[f'{q:+.0%}' for q in s['qs']]}, p={s['p']:.4f}")

    print("\n=== (4) cross-universe replication (UNIVERSE_B, 54 different NSE names) on the top 2 ===")
    SYMBOLS = UNIVERSE_B
    Mb = load_matrices()
    SYMBOLS = None
    years_b = (Mb["close"].index[Mb["me"][-1]] - Mb["close"].index[Mb["me"][0]]).days / 365.25
    Sb = scores(Mb, "ibs", 5)
    atrb = compute_atr(Mb)
    base_b = simulate(Mb, Sb, top_k, 1)
    print(f"Universe-B baseline (no stop): {cagr(base_b['final'], years_b):6.2f}%/yr, maxDD {base_b['max_dd']:.1%}")
    for stop_mult in best_two:
        s = _cell_stats(Mb, Sb, atrb, top_k, stop_mult, years_b, seeds)
        print(f"  stop={stop_mult:.2f}xATR (univ B): {s['ann']:6.2f}%/yr, maxDD {s['r']['max_dd']:.1%}, Calmar {s['calmar']:.2f}, "
              f"halves {s['h1']:+.0%}/{s['h2']:+.0%} {'CONSISTENT' if s['consistent'] else 'INCONSISTENT'}, "
              f"quarters {[f'{q:+.0%}' for q in s['qs']]}, p={s['p']:.4f}")


def build_gate(M, nifty_full, L):
    """Boolean risk-on array aligned to M's own calendar: NIFTY close > its own
    L-day SMA (full, pre-backtest history, so no SMA warm-up falls inside the
    window), known at the ranking close before the lag-1 fill. Shared by
    gate_test and gate_full_rigor so the gate definition can't drift between
    the two call sites."""
    align = lambda x: x.reindex(M["close"].index.union(x.index)).ffill().reindex(M["close"].index)
    nifty = align(nifty_full)
    sma = align(nifty_full.rolling(L, min_periods=L).mean())
    return np.where(sma.isna(), True, nifty > sma)  # (nifty > NaN) is False, so fillna would never fire


def _gate_cell_stats(M, S, gate, top_k, years, seeds, rng_seed=0):
    r = simulate(M, S, top_k, 1, gate=gate)
    off = [a for a in M["me"][:-1] if not gate[a]]
    ann = cagr(r["final"], years)
    calmar = ann / (100 * r["max_dd"]) if r["max_dd"] else float("nan")
    mo = r["months"]
    half = len(mo) // 2
    h1, h2 = np.prod(1 + mo[:half]) - 1, np.prod(1 + mo[half:]) - 1
    consistent = (h1 > 0) == (h2 > 0)
    qs = [np.prod(1 + q) - 1 for q in np.array_split(mo, 4)]
    rng = np.random.default_rng(rng_seed)
    finals, dds = [], []
    for _ in range(seeds):
        idx = set(rng.choice(M["me"][:-1], size=len(off), replace=False)) if off else set()
        g = np.ones(len(gate), bool)
        for i in idx:
            g[i] = False
        rr = simulate(M, S, top_k, 1, gate=g)
        finals.append(rr["final"])
        dds.append(rr["max_dd"])
    finals, dds = np.array(finals), np.array(dds)
    p = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
    pdd = ((dds <= r["max_dd"]).sum() + 1) / (seeds + 1)
    return dict(r=r, off=off, ann=ann, calmar=calmar, h1=h1, h2=h2, consistent=consistent, qs=qs,
                p=p, pdd=pdd, random_mean=cagr(finals.mean(), years), random_dd=dds.mean())


def gate_cell_detail(M, nifty_full, years, seeds, L, top_k=5):
    """Annualized quarter-split (with its own random-off-months control per quarter)
    for one SMA-length cell — gate_test's own quarters print total return per chunk
    only, same gap atr_stop_cell_detail closed for the ATR-stop line."""
    S = scores(M, "ibs", 5)
    gate = build_gate(M, nifty_full, L)
    r = simulate(M, S, top_k, 1, gate=gate)
    off = [a for a in M["me"][:-1] if not gate[a]]
    mo = r["months"]
    n = len(mo)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n]
    print(f"IBS(5) top_k={top_k} lag=1, SMA{L} gate — quarter-split detail:")
    rng = np.random.default_rng(1)
    random_mo = []
    for _ in range(min(seeds, 300)):
        idx = set(rng.choice(M["me"][:-1], size=len(off), replace=False)) if off else set()
        g = np.ones(len(gate), bool)
        for i in idx:
            g[i] = False
        random_mo.append(simulate(M, S, top_k, 1, gate=g)["months"])
    for i in range(4):
        lo, hi = cuts[i], cuts[i + 1]
        ch = mo[lo:hi]
        yrs_q = len(ch) / 12
        total = np.prod(1 + ch) - 1
        ann = ((1 + total) ** (1 / yrs_q) - 1) * 100 if yrs_q > 0 and total > -1 else float("nan")
        rand_totals = np.array([np.prod(1 + rm[lo:hi]) - 1 for rm in random_mo if len(rm) >= hi])
        p_q = ((rand_totals >= total).sum() + 1) / (len(rand_totals) + 1) if len(rand_totals) else float("nan")
        print(f"  Q{i + 1} ({len(ch)} months, ~{yrs_q:.1f}y): total {total:+.1%}, annualized {ann:+.2f}%/yr, "
              f"random-control mean {rand_totals.mean():+.1%}, p={p_q:.4f}")


def gate_full_rigor(top_k=5, seeds=1500):
    """Full-rigor pass on the NIFTY SMA trend gate (Sixty-first/Seventy-sixth entries),
    the same four-check battery just applied to the ATR-stop-only variant: (1) screening
    on the base 52-stock universe (SMA in {100,150,200}), (2) quarter-split detail on the
    two best-by-Calmar cells, (3) survivorship stress (4 real blowups, 56 stocks),
    (4) cross-universe replication (UNIVERSE_B, 54 different NSE names) — the one check
    this overlay had NOT yet been put through, unlike (1)-(3) which earlier entries
    already covered. A cell only counts as a real survivor if it clears ALL four."""
    global SYMBOLS
    import probe_macro_analog as pm
    nifty_full = pm.load()["nifty"].dropna()

    print("=== (1) screening: SMA length grid on the base 52-stock universe ===")
    M = load_matrices()
    years = (M["close"].index[M["me"][-1]] - M["close"].index[M["me"][0]]).days / 365.25
    S = scores(M, "ibs", 5)
    base = simulate(M, S, top_k, 1)
    print(f"baseline (ungated): {cagr(base['final'], years):6.2f}%/yr, maxDD {base['max_dd']:.1%}, "
          f"Calmar {cagr(base['final'], years) / (100 * base['max_dd']):.2f}")
    grid = (100, 150, 200)
    cells = {}
    for L in grid:
        gate = build_gate(M, nifty_full, L)
        s = _gate_cell_stats(M, S, gate, top_k, years, seeds)
        cells[L] = s
        print(f"  SMA{L}: {len(s['off'])}/{len(M['me']) - 1} months in cash -> {s['ann']:6.2f}%/yr, "
              f"maxDD {s['r']['max_dd']:.1%}, Calmar {s['calmar']:.2f}, halves {s['h1']:+.0%}/{s['h2']:+.0%} "
              f"{'CONSISTENT' if s['consistent'] else 'INCONSISTENT'}, quarters {[f'{q:+.0%}' for q in s['qs']]}")
        print(f"    vs random same-count off-months: return {s['random_mean']:.2f}%/yr (p={s['p']:.3f}), "
              f"DD {s['random_dd']:.1%} (p={s['pdd']:.4f})")

    best_two = sorted(grid, key=lambda L: -cells[L]["calmar"])[:2]
    print(f"\ntop 2 by Calmar: SMA{best_two[0]} (Calmar {cells[best_two[0]]['calmar']:.2f}), "
          f"SMA{best_two[1]} (Calmar {cells[best_two[1]]['calmar']:.2f})")

    print("\n=== (2) quarter-split detail (annualized, own random control per quarter) on the top 2 ===")
    for L in best_two:
        gate_cell_detail(M, nifty_full, years, seeds, L, top_k)

    print("\n=== (3) survivorship stress (4 real blowups added, 56 stocks) on the top 2 ===")
    Ms = load_matrices(stress=True)
    years_s = (Ms["close"].index[Ms["me"][-1]] - Ms["close"].index[Ms["me"][0]]).days / 365.25
    Ss = scores(Ms, "ibs", 5)
    base_s = simulate(Ms, Ss, top_k, 1)
    print(f"stress-universe baseline (ungated): {cagr(base_s['final'], years_s):6.2f}%/yr, maxDD {base_s['max_dd']:.1%}")
    for L in best_two:
        gate = build_gate(Ms, nifty_full, L)
        s = _gate_cell_stats(Ms, Ss, gate, top_k, years_s, seeds)
        print(f"  SMA{L} (stress): {s['ann']:6.2f}%/yr, maxDD {s['r']['max_dd']:.1%}, Calmar {s['calmar']:.2f}, "
              f"halves {s['h1']:+.0%}/{s['h2']:+.0%} {'CONSISTENT' if s['consistent'] else 'INCONSISTENT'}, "
              f"quarters {[f'{q:+.0%}' for q in s['qs']]}, p(return)={s['p']:.4f}, p(DD)={s['pdd']:.4f}")

    print("\n=== (4) cross-universe replication (UNIVERSE_B, 54 different NSE names) on the top 2 ===")
    SYMBOLS = UNIVERSE_B
    Mb = load_matrices()
    SYMBOLS = None
    years_b = (Mb["close"].index[Mb["me"][-1]] - Mb["close"].index[Mb["me"][0]]).days / 365.25
    Sb = scores(Mb, "ibs", 5)
    base_b = simulate(Mb, Sb, top_k, 1)
    print(f"Universe-B baseline (ungated): {cagr(base_b['final'], years_b):6.2f}%/yr, maxDD {base_b['max_dd']:.1%}")
    for L in best_two:
        gate = build_gate(Mb, nifty_full, L)
        s = _gate_cell_stats(Mb, Sb, gate, top_k, years_b, seeds)
        print(f"  SMA{L} (univ B): {s['ann']:6.2f}%/yr, maxDD {s['r']['max_dd']:.1%}, Calmar {s['calmar']:.2f}, "
              f"halves {s['h1']:+.0%}/{s['h2']:+.0%} {'CONSISTENT' if s['consistent'] else 'INCONSISTENT'}, "
              f"quarters {[f'{q:+.0%}' for q in s['qs']]}, p(return)={s['p']:.4f}, p(DD)={s['pdd']:.4f}")


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
    ap.add_argument("--skew", action="store_true", help="realized-skewness rotation (Eighty-seventh entry)")
    ap.add_argument("--freq", action="store_true", help="IBS rebalance frequency 5/10/21 days")
    ap.add_argument("--phase", action="store_true", help="IBS 21d-step phase offsets vs calendar month-end")
    ap.add_argument("--anchor", action="store_true", help="IBS/rev rebalance j days before month-end, j=0..20")
    ap.add_argument("--oos", action="store_true", help="month-end window on the pre-2016 decade")
    ap.add_argument("--horizon", action="store_true", help="IBS excess-return curve by holding horizon")
    ap.add_argument("--hold", action="store_true", help="post-hoc: hold h days from month-end entry then cash")
    ap.add_argument("--atr-stop", action="store_true", help="IBS rotation with an ATR-based stop/target during the hold")
    ap.add_argument("--atr-stop-detail", action="store_true", help="quarter-split detail for stop=1.0xATR target=4.0xATR")
    ap.add_argument("--atr-stop-perturb", action="store_true", help="perturbation sweep around stop=1.0xATR target=4.0xATR")
    ap.add_argument("--atr-target-widen", action="store_true", help="widen target_mult to find where the curve turns over")
    ap.add_argument("--stop-only-rigor", action="store_true", help="full-rigor pass on the ATR-stop-only variant")
    ap.add_argument("--gate-rigor", action="store_true", help="full-rigor pass on the NIFTY SMA trend gate")
    ap.add_argument("--oos-horizon", action="store_true", help="horizon curve on 2007-2016 vs 2016-2026")
    ap.add_argument("--universe-b", action="store_true", help="52 different NSE names (Sixty-eighth entry)")
    ap.add_argument("--cost", type=float, help="per-leg cost %% override (default 0.2; ~0.125 is nearer real NSE delivery costs)")
    ap.add_argument("--us", action="store_true", help="month-end tests on US large caps (Seventy-fourth entry)")
    ap.add_argument("--extend", action="store_true", help="EXPLORATORY: windows/top_k beyond the pre-registered grid")
    a = ap.parse_args()
    if a.cost is not None:
        globals()["COST_PCT"] = a.cost
    if a.us:
        return us_test(a.seeds, a.cost if a.cost is not None else 0.05)
    if a.universe_b:
        global SYMBOLS
        SYMBOLS = UNIVERSE_B
    if a.index_gate:
        return index_gate_replication(a.seeds)
    if a.oos:
        return oos_anchor_test()
    if a.oos_horizon:
        return oos_horizon()
    if a.stop_only_rigor:
        return stop_only_full_rigor(seeds=a.seeds)
    if a.gate_rigor:
        return gate_full_rigor(seeds=a.seeds)
    M = load_matrices(stress=a.stress)
    years = (M["close"].index[M["me"][-1]] - M["close"].index[M["me"][0]]).days / 365.25
    print(f"universe {M['close'].shape[1]} stocks, {len(M['me'])} month-ends, {years:.1f}y")
    if a.validate:
        print("VALIDATION: lag=0 IBS(5) should be ~22%/yr (family's Thirty-ninth-entry number)")
        print(report(M, "ibs", 5, 5, 0, 0, years)[0])
        return
    if a.overlap:
        return overlap(M, years)
    if a.hold:
        return hold_test(M, years, a.seeds)
    if a.atr_stop:
        return atr_stop_test(M, years, a.seeds)
    if a.atr_stop_detail:
        return atr_stop_cell_detail(M, years, a.seeds, 1.0, 4.0)
    if a.atr_stop_perturb:
        return atr_stop_perturb(M, years, a.seeds)
    if a.atr_target_widen:
        return atr_target_widen(M, years)
    if a.horizon:
        return horizon_curve(M)
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
    if a.skew:
        print(f"=== realized-skewness rotation, lag 1, {a.seeds}-seed control (pre-registered 2 windows x top_k 3/5/8) ===")
        for window in (21, 63):
            for top_k in (3, 5, 8):
                print(report(M, "skew", window, top_k, 1, a.seeds, years)[0])
        return
    if a.spread:
        return spread_test(M, years, a.seeds)
    if a.gate:
        return gate_test(M, years, a.seeds)
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
