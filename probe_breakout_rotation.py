"""Probe: does the cross-sectional-rank recipe (Thirty-eighth through
Forty-second entries) generalize to this project's two remaining "real but
thin" single-instrument survivors - 3-bar compression breakout (Twelfth
entry, this project's best single-instrument hit rate, 50%) and Turtle
Soup failed-breakout fade (Twentieth entry, smoothest single-survivor
perturbation sweep)?

Both strategies' own check_entry() needs a stock's OWN high/low, not just
closes, so - same shape-mismatch reasoning already applied throughout this
project (probe_ibs.py, probe_supertrend.py, etc.) - they're reimplemented
here as continuous monthly scores rather than reusing daily_strategy.py's
interface. Same scaffolding as probe_ibs_rotation_widen.py/
probe_rsi2_rotation.py: 52-stock WIDE_UNIVERSE, monthly rebalance, the same
zero-delivery-brokerage equity cost model, the same 1,500-seed
random-control significance methodology.

Score definitions (both long-only, mirroring IBS/RSI-2 rotation's own
ascending "most negative = most attractive, ranked first" convention):

- **3-bar breakout**: bar1/bar2 (the two bars before the as-of date) must
  be compressed within `compression_atr_mult` x ATR of each other (the
  source thread's own filter, ThreeBarBreakoutStrategy's own condition);
  if bar3 (the as-of bar) closes above both, score =
  -(bar3_close - max(bar1, bar2))/ATR - the breakout's size in ATR units,
  negated so a BIGGER breakout ranks first. No compression, no breakout,
  or a downside breakout (this project's rotation strategies are
  long-only, matching IBS/RSI-2 rotation) -> no score, excluded that month.
- **Turtle Soup**: yesterday must have closed below the prior
  `channel_period`-day low (excluding yesterday itself, same as
  TurtleSoupStrategy's own `lowest(self._lows[:-1], ...)`) AND today must
  have closed back above it. score = -(depth_broken + recovery_size)/ATR,
  both terms in ATR units - a deeper break combined with a stronger
  recovery ranks first. No active fade signal -> no score, excluded.
"""
import argparse
import bisect

from indicators import average_true_range, lowest
from probe_ibs_rotation import (
    price_at_or_before, dates_closes_maps, _report,
)
from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series
from probe_ibs_rotation import month_end_dates, fetch_calendar


def threebar_score(candles: list[dict], dates: list, as_of,
                    atr_period: int = 14, compression_atr_mult: float = 0.5) -> float | None:
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < atr_period + 2:
        return None
    bar1, bar2, bar3 = candles[idx - 2], candles[idx - 1], candles[idx]
    atr = average_true_range(candles[:idx], period=atr_period)  # through bar2, no lookahead into bar3
    if atr is None or atr <= 0:
        return None
    if abs(bar1["close"] - bar2["close"]) > compression_atr_mult * atr:
        return None
    breakout = bar3["close"] - max(bar1["close"], bar2["close"])
    if breakout <= 0:
        return None
    return -(breakout / atr)


def turtlesoup_score(candles: list[dict], dates: list, as_of,
                      channel_period: int = 20, atr_period: int = 14) -> float | None:
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < max(channel_period, atr_period) + 2:
        return None
    lows = [c["low"] for c in candles[:idx]]  # through yesterday (idx-1), excludes today
    prior_low = lowest(lows[:-1], channel_period)  # excludes yesterday itself, mirrors self._lows[:-1]
    if prior_low is None:
        return None
    yesterday_close = candles[idx - 1]["close"]
    today_close = candles[idx]["close"]
    if yesterday_close >= prior_low or today_close <= prior_low:
        return None
    atr = average_true_range(candles[:idx], period=atr_period)  # through yesterday
    if atr is None or atr <= 0:
        return None
    depth = (prior_low - yesterday_close) / atr
    recovery = (today_close - prior_low) / atr
    return -(depth + recovery)


SCORERS = {"threebar": threebar_score, "turtlesoup": turtlesoup_score}


def rank(series: dict, dates_map: dict, closes_map: dict, as_of, top_k: int, scorer) -> list:
    scored = []
    for sym, candles in series.items():
        score = scorer(candles, dates_map[sym], as_of)
        px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if score is None or px is None:
            continue
        scored.append((score, sym, px))
    scored.sort(key=lambda t: t[0])
    return scored[:top_k]


def simulate(rebalance_dates: list, series: dict, strategy: str = "threebar", top_k: int = 5,
             cost_pct: float = 0.2, dp_charge: float = 16.0, capital: float = 100_000.0):
    scorer = SCORERS[strategy]
    dates_map, closes_map = dates_closes_maps(series)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        picks = rank(series, dates_map, closes_map, entry_date, top_k, scorer)
        picks = [(sym, entry_px, price_at_or_before(dates_map[sym], closes_map[sym], exit_date))
                 for _, sym, entry_px in picks]
        picks = [p for p in picks if p[2] is not None]
        if not picks:
            months.append(dict(date=entry_date, pnl=0.0, n=0))
            continue
        notional_each = capital_track / len(picks)
        month_pnl = 0.0
        for _, entry_px, exit_px in picks:
            ret = (exit_px - entry_px) / entry_px
            gross = notional_each * ret
            cost = notional_each * (cost_pct / 100) * 2 + dp_charge
            month_pnl += gross - cost
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=len(picks)))

    total_net = sum(m["pnl"] for m in months)
    traded = [m for m in months if m["n"] > 0]
    wins = sum(1 for m in traded if m["pnl"] > 0)
    return dict(months=months, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_months=len(months),
                win_rate=wins / len(traded) if traded else 0.0)


def quarter_split(rebalance_dates, series, **kwargs):
    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    results = []
    for i in range(4):
        chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
        if len(chunk) < 2:
            results.append(None)
            continue
        results.append(simulate(chunk, series, **kwargs))
    return results


def full_check(strategy="threebar", top_k=5, period="10y", capital=100_000.0):
    print(f"{strategy} rotation on {len(WIDE_UNIVERSE)}-stock WIDE_UNIVERSE, top_k={top_k}")
    series = build_wide_price_series(period)
    rebalance_dates = month_end_dates(fetch_calendar(period))
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(strategy=strategy, top_k=top_k, capital=capital)

    cutoff = len(rebalance_dates) // 2
    in_s = simulate(rebalance_dates[:cutoff + 1], series, **kwargs)
    out_s = simulate(rebalance_dates[cutoff:], series, **kwargs)
    _report("In-sample  (first half)", in_s, capital, years / 2)
    _report("Out-of-sample (2nd half)", out_s, capital, years / 2)
    consistent = (in_s["final_capital"] > capital) == (out_s["final_capital"] > capital)
    print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves\n")

    quarter_pnls = []
    for i, r in enumerate(quarter_split(rebalance_dates, series, **kwargs), start=1):
        if r is None:
            print(f"Q{i}: insufficient data")
            continue
        quarter_pnls.append(r["total_net"])
        _report(f"Q{i}", r, capital, years / 4)
    print(f"all 4 quarters positive: {all(p > 0 for p in quarter_pnls)}\n")

    full = simulate(rebalance_dates, series, **kwargs)
    _report("Full period", full, capital, years)
    return series, rebalance_dates


def simulate_random(rebalance_dates, series, seed, top_k=5, capital=100_000.0):
    """Same mechanics as probe_ibs_rotation_significance.simulate_random
    (random top_k names each month instead of a ranked pick) - reimplemented
    here rather than imported since that module's version is hardcoded to
    probe_ibs_rotation's simulate() signature (lookback kwarg this module's
    simulate() doesn't take)."""
    import random
    dates_map, closes_map = dates_closes_maps(series)
    rng = random.Random(seed)
    capital_track = capital
    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        eligible = [s for s in series if price_at_or_before(dates_map[s], closes_map[s], entry_date) is not None]
        picks = rng.sample(eligible, min(top_k, len(eligible)))
        notional_each = capital_track / len(picks) if picks else 0.0
        month_pnl = 0.0
        for sym in picks:
            entry_px = price_at_or_before(dates_map[sym], closes_map[sym], entry_date)
            exit_px = price_at_or_before(dates_map[sym], closes_map[sym], exit_date)
            if entry_px is None or exit_px is None:
                continue
            ret = (exit_px - entry_px) / entry_px
            gross = notional_each * ret
            cost = notional_each * 0.004 + 16.0
            month_pnl += gross - cost
        capital_track += month_pnl
    return capital_track


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["threebar", "turtlesoup"], default="threebar")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--significance", action="store_true")
    parser.add_argument("--n-seeds", type=int, default=1500)
    args = parser.parse_args()

    if args.significance:
        series = build_wide_price_series("10y")
        rebalance_dates = month_end_dates(fetch_calendar("10y"))
        for top_k in (3, 5, 8):
            actual = simulate(rebalance_dates, series, strategy=args.strategy, top_k=top_k, capital=args.capital)
            randoms = [simulate_random(rebalance_dates, series, seed, top_k=top_k, capital=args.capital)
                       for seed in range(args.n_seeds)]
            randoms.sort()
            n_beat = sum(1 for r in randoms if r >= actual["final_capital"])
            mean_r = sum(randoms) / len(randoms)
            std_r = (sum((r - mean_r) ** 2 for r in randoms) / len(randoms)) ** 0.5
            z = (actual["final_capital"] - mean_r) / std_r if std_r else float("nan")
            pct = 100 * sum(1 for r in randoms if r < actual["final_capital"]) / len(randoms)
            print(f"{args.strategy} top_k={top_k}: actual={actual['final_capital']:.0f} random_mean={mean_r:.0f} "
                  f"random_std={std_r:.0f} percentile={pct:.1f} empirical_p={n_beat/len(randoms):.4f} z={z:.2f}")
    else:
        full_check(strategy=args.strategy, top_k=args.top_k, capital=args.capital)
