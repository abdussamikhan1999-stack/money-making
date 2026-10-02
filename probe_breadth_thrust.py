"""
Hundred-and-first entry: Zweig Breadth Thrust (Martin Zweig, "Winning on Wall
Street", 1986) -- a market-BREADTH signal never tested in this project. Every
prior market-timing overlay here (Sixty-first/Sixty-second trend gates,
Fifty-seventh/Fifty-eighth VIX-spike fear-buy) conditions on a SINGLE
instrument's own price or implied volatility. Breadth conditions on how many
DIFFERENT stocks are participating in a move -- a genuinely different data
dimension (cross-sectional agreement, not one series' own level) -- and this
project already has the raw ingredient (the 52/54-stock universes' daily
closes, fetched throughout Entries 38-99) but has never aggregated them into
a market-breadth statistic.

Zweig's own published rule, implemented exactly as stated (unusually precise
for a decades-old technical rule, so no reinterpretation needed):
  - Daily breadth ratio = advances / (advances + declines) across the
    universe; unchanged names excluded from both, the standard convention.
  - "10% trend" = an EXPONENTIAL moving average of that ratio with smoothing
    constant 0.1 (alpha=0.1) -- NOT a 10-day window. Zweig's own terminology
    ("10% trend") means today's trend = yesterday's trend + 10% of the gap
    to today's raw ratio.
  - A "thrust" fires when the 10% trend rises from <= 0.40 to >= 0.615
    within 10 trading days or fewer -- a rare, large, rapid swing from
    oversold to strongly positive breadth. Zweig's own claim (NYSE data,
    1945-1986): every such thrust was followed by a strong advance over the
    following months, with only a handful of occurrences in 40 years --
    EXPLICITLY a rare, low-n signal by the letter of the rule, not a
    parameter this project is choosing to make thin.

Tested on two independent 52/54-stock NSE universes already used throughout
this project (WIDE_UNIVERSE, UNIVERSE_B) as breadth sources, 20y (the
longest common period both universes can reach -- deliberately longer than
this project's usual 10y rotation window specifically so a rare signal like
this has a real chance to fire at all, and so it can be checked against
2009's crash bottom, historically one of the most famous real-world
Zweig-thrust-qualifying events in US markets). Forward return on NIFTY at
several horizons, lag-1 fill (decision known at the event day's close, enter
the NEXT close -- this project's standard no-lookahead convention), against
the random-day-pool null used throughout the calendar/event-study entries
(Ninety-second onward, via probe_fear_followup.random_pool_p). Given the
rule's own built-in rarity this is explicitly a low-n descriptive study, not
a well-powered significance test -- flagged up front, the same discipline
the macro-analog entry (Fifty-seventh) applied to its own n=10 oil-shock
episodes.

Secondary, cheap reuse of the same breadth data (universe A, 10y -- matching
the window the Sixty-first/Seventy-sixth entries' own price-SMA gate used,
for direct comparability): a continuous "% of universe above its own 200-day
SMA" gate on the IBS rotation, same simulate()/gate= machinery, same
random-off-months control. Does breadth-based timing beat single-index-price
timing for the one strategy this project actually tracks?
"""
import argparse

import numpy as np
import pandas as pd

import probe_macro_analog as pm
import probe_reversal_rotation as rr
from probe_fear_followup import declustered_events, random_pool_p
from probe_reversal_rotation import cagr, load_matrices, scores, simulate


def breadth_ratio(M):
    """Daily advance/decline ratio across the universe in M['close']:
    advances / (advances + declines); unchanged days excluded from both."""
    ret = M["close"].pct_change()
    adv = (ret > 0).sum(axis=1)
    dec = (ret < 0).sum(axis=1)
    total = adv + dec
    return adv / total.replace(0, np.nan)


def zweig_trend(breadth, alpha=0.10):
    """Zweig's own '10% trend': EMA with smoothing constant 0.1 (not a
    10-day window)."""
    return breadth.ewm(alpha=alpha, adjust=False).mean()


def thrust_events(trend, lookback=10, low=0.40, high=0.615, gap=15):
    """A thrust fires on day i if trend[i] >= high and trend dipped <= low
    at some point in the previous `lookback` trading days. Declustered to
    one event per episode (>= `gap` trading days apart)."""
    tv = trend.to_numpy()
    fired = np.zeros(len(tv), dtype=bool)
    for i in range(len(tv)):
        if np.isnan(tv[i]) or tv[i] < high:
            continue
        window = tv[max(0, i - lookback):i]
        window = window[~np.isnan(window)]
        if window.size and window.min() <= low:
            fired[i] = True
    return declustered_events(fired, gap=gap)


def align_nifty(M):
    nifty_full = pm.load()["nifty"].dropna()
    return nifty_full.reindex(M["close"].index.union(nifty_full.index)).ffill().reindex(M["close"].index)


def diagnostics(trend, label):
    tv = trend.to_numpy()
    n_low = int((tv <= 0.40).sum())
    n_high = int((tv >= 0.615).sum())
    lows = np.flatnonzero(tv <= 0.40)
    highs = np.flatnonzero(tv >= 0.615)
    best = None
    for lo in lows:
        nxt = highs[highs > lo]
        if len(nxt) and (best is None or nxt[0] - lo < best[0]):
            best = (int(nxt[0] - lo), int(lo), int(nxt[0]))
    print(f"{label}: trend range {np.nanmin(tv):.3f}-{np.nanmax(tv):.3f}; "
          f"{n_low} days <=0.40, {n_high} days >=0.615; "
          f"fastest low->high transition (no window cap): "
          f"{best[0] if best else 'none'} trading days" + (
              f" ({trend.index[best[1]].date()} -> {trend.index[best[2]].date()})" if best else ""))


def event_study(nifty, events, dates, horizons=(21, 63, 126, 252), label=""):
    close = nifty.to_numpy()
    n = len(close)
    print(f"\n--- {label}: {len(events)} thrust event(s) (literal 10-day-window rule) ---")
    rows = []
    if len(events) == 0:
        print("  none in this 20y window -- nothing to test against the literal rule")
        return rows
    for e in events:
        print(f"  event: {dates[e].date()}")
    for h in horizons:
        fwd_all = close[h + 1:] / close[1:n - h] - 1 if n - h - 1 > 0 else np.array([])
        valid = [e for e in events if e + 1 + h < n]
        if not valid:
            continue
        ev_fwd = np.array([close[e + 1 + h] / close[e + 1] - 1 for e in valid])
        pool = fwd_all[~np.isnan(fwd_all)]
        p = random_pool_p(pool, ev_fwd, n_draws=20_000)
        print(f"  h={h:3d}d: n={len(ev_fwd)}, mean {ev_fwd.mean():+.2%}, median {np.median(ev_fwd):+.2%}, "
              f"positive {int((ev_fwd > 0).sum())}/{len(ev_fwd)}, min {ev_fwd.min():+.2%}, "
              f"unconditional mean {pool.mean():+.2%}, random-day p={p:.4f}")
        rows.append((f"Breadth thrust {label} h={h} (Hundred-and-first)", p))
    return rows


def relaxed_window_check(trend, nifty, dates, label, lookback=15, horizons=(21, 63, 126, 252)):
    """EXPLORATORY, post hoc, NOT pre-registered and NOT a significance
    test: widening the literal rule's 10-day window to 15 days to see
    whether the near-miss episode(s) this project's own diagnostics
    surface are worth a descriptive look. n is too small here for any
    p-value to mean anything -- reported as raw numbers only, same
    discipline the macro-analog entry (Fifty-seventh) used for its own
    single-digit-n episodes."""
    events = thrust_events(trend, lookback=lookback)
    print(f"\n--- {label}: relaxed {lookback}-day window (exploratory, post hoc, n too small for a p-value) ---")
    if len(events) == 0:
        print("  still none")
        return
    close = nifty.to_numpy()
    n = len(close)
    for e in events:
        print(f"  event: {dates[e].date()}")
        for h in horizons:
            if e + 1 + h >= n:
                continue
            r = close[e + 1 + h] / close[e + 1] - 1
            print(f"    +{h:3d}d: {r:+.2%}")


def breadth_gate_test(M, years, seeds=2000):
    """% of universe above its own 200d SMA as a gate on IBS rotation,
    directly comparable to the price-only NIFTY-SMA gate (Sixty-first/
    Seventy-sixth entries): same simulate()/gate= call, same random-off-
    months control."""
    sma200 = M["close"].rolling(200, min_periods=200).mean()
    valid = sma200.notna()
    above = (M["close"] > sma200) & valid
    denom = valid.sum(axis=1).replace(0, np.nan)
    pct_above = (above.sum(axis=1) / denom).reindex(M["close"].index)
    gate = np.where(pct_above.isna(), True, pct_above.to_numpy() > 0.5)

    S = scores(M, "ibs", 5)
    rows = []
    for top_k in (5, 8):
        base = simulate(M, S, top_k, 1)
        r = simulate(M, S, top_k, 1, gate=gate)
        off = [a for a in M["me"][:-1] if not gate[a]]
        rng = np.random.default_rng(0)
        finals, dds = [], []
        for _ in range(seeds):
            idx = set(rng.choice(M["me"][:-1], size=len(off), replace=False)) if off else set()
            g = np.ones(len(gate), bool)
            for i in idx:
                g[i] = False
            rr_ = simulate(M, S, top_k, 1, gate=g)
            finals.append(rr_["final"])
            dds.append(rr_["max_dd"])
        finals = np.array(finals)
        p_ret = ((finals >= r["final"]).sum() + 1) / (seeds + 1)
        p_dd = ((np.array(dds) <= r["max_dd"]).sum() + 1) / (seeds + 1)
        print(f"\ntop_k={top_k}: ungated {cagr(base['final'], years):.2f}%/yr maxDD {base['max_dd']:.1%}  |  "
              f"breadth-gated ({len(off)}/{len(M['me']) - 1} months in cash) {cagr(r['final'], years):.2f}%/yr "
              f"maxDD {r['max_dd']:.1%}  |  random-off-months {cagr(finals.mean(), years):.2f}%/yr "
              f"DD {np.mean(dds):.1%}  |  p(return)={p_ret:.3f} p(drawdown as low)={p_dd:.3f}")
        rows.append((f"Breadth-pct-above-200sma gate top_k={top_k} return (Hundred-and-first)", p_ret))
        rows.append((f"Breadth-pct-above-200sma gate top_k={top_k} drawdown (Hundred-and-first)", p_dd))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="20y")
    ap.add_argument("--gate-period", default="10y")
    ap.add_argument("--skip-gate", action="store_true")
    args = ap.parse_args()

    all_rows = []

    print(f"=== universe A (WIDE_UNIVERSE, 52 stocks), {args.period} ===")
    M = load_matrices(args.period)
    trend_a = zweig_trend(breadth_ratio(M))
    diagnostics(trend_a, "universe A")
    ev_a = thrust_events(trend_a)
    nifty_a = align_nifty(M)
    all_rows += event_study(nifty_a, ev_a, M["close"].index, label="universe A")
    relaxed_window_check(trend_a, nifty_a, M["close"].index, "universe A")

    print(f"\n=== universe B (54 different NSE stocks, cross-check), {args.period} ===")
    rr.SYMBOLS = rr.UNIVERSE_B
    MB = load_matrices(args.period)
    rr.SYMBOLS = None
    trend_b = zweig_trend(breadth_ratio(MB))
    diagnostics(trend_b, "universe B")
    ev_b = thrust_events(trend_b)
    nifty_b = align_nifty(MB)
    all_rows += event_study(nifty_b, ev_b, MB["close"].index, label="universe B")
    relaxed_window_check(trend_b, nifty_b, MB["close"].index, "universe B")

    common = sorted(set(M["close"].index[i] for i in ev_a) & set(MB["close"].index[j] for j in ev_b))
    print(f"\nliteral-rule event dates in common between the two universes: "
          f"{[str(d.date()) for d in common] if common else 'n/a (zero events on at least one side)'}")

    if not args.skip_gate:
        print(f"\n=== breadth-level gate on IBS rotation (universe A, {args.gate_period}) ===")
        Mg = load_matrices(args.gate_period) if args.gate_period != args.period else M
        years = (Mg["close"].index[-1] - Mg["close"].index[0]).days / 365.25
        all_rows += breadth_gate_test(Mg, years)

    print("\n=== summary (uncorrected p < 0.05) ===")
    for label, p in all_rows:
        if p < 0.05:
            print(f"  {label}: p={p:.4f}")
    print(f"\n{len(all_rows)} p-values produced this run (for registration in multiple_comparisons.py)")


if __name__ == "__main__":
    main()
