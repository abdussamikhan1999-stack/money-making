"""Probe: does the Thirty-eighth/Thirty-ninth entries' "cross-sectional
monthly rank" RECIPE generalize beyond IBS, or was the win specific to
IBS's particular mean-reversion character?

Same shape as probe_ibs_rotation_widen.py (monthly rank the 52-stock
WIDE_UNIVERSE, long the top_k most-oversold names, equal-weight, monthly
rebalance, same zero-delivery-brokerage equity cost model) but the ranking
signal is swapped for Connors RSI(2) — this project's own most-cited
single-instrument survivor (Third entry) — using the identical formula
ConnorsRSI2Strategy.check_entry() uses (`indicators.rsi(closes, period=2)`,
default seed_window=20, so a name needs >=21 trailing closes to score).
Reuses probe_ibs_rotation_widen.py's universe/price-fetch/calendar
machinery and probe_ibs_rotation_significance.py's random-control
methodology unchanged — only the ranking function and its unit test are
new code.
"""
import argparse
import bisect

from indicators import rsi
from probe_ibs_rotation import (
    price_at_or_before, dates_closes_maps, annualized_return_pct, _report,
)
from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series
from probe_ibs_rotation import month_end_dates, fetch_calendar
from probe_ibs_rotation_significance import simulate_random


def rsi2_score(closes: list[float], dates: list, as_of) -> float | None:
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return None
    return rsi(closes[:idx + 1], period=2)


def rank_by_rsi2(series: dict, dates_map: dict, closes_map: dict, as_of, top_k: int):
    """Ascending RSI(2) first (most oversold), mirroring probe_ibs_rotation's
    rank_by_ibs — lower RSI(2) = more oversold = picked first."""
    scored = []
    for sym in series:
        score = rsi2_score(closes_map[sym], dates_map[sym], as_of)
        px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if score is None or px is None:
            continue
        scored.append((score, sym, px))
    scored.sort(key=lambda t: t[0])
    return scored[:top_k]


def simulate(rebalance_dates: list, series: dict, top_k: int = 5,
             cost_pct: float = 0.2, dp_charge: float = 16.0, capital: float = 100_000.0):
    dates_map, closes_map = dates_closes_maps(series)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        picks = rank_by_rsi2(series, dates_map, closes_map, entry_date, top_k)
        picks = [(sym, entry_px, price_at_or_before(dates_map[sym], closes_map[sym], exit_date))
                 for _, sym, entry_px in picks]
        picks = [p for p in picks if p[2] is not None]
        if not picks:
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
    wins = sum(1 for m in months if m["pnl"] > 0)
    return dict(months=months, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_months=len(months), win_rate=wins / len(months) if months else 0.0)


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


def full_check(top_k=5, period="10y", capital=100_000.0):
    print(f"RSI-2 rotation on {len(WIDE_UNIVERSE)}-stock WIDE_UNIVERSE, top_k={top_k}")
    series = build_wide_price_series(period)
    rebalance_dates = month_end_dates(fetch_calendar(period))
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, capital=capital)

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--significance", action="store_true")
    parser.add_argument("--n-seeds", type=int, default=1500)
    args = parser.parse_args()

    if args.significance:
        series = build_wide_price_series("10y")
        rebalance_dates = month_end_dates(fetch_calendar("10y"))
        for top_k in (3, 5, 8):
            actual = simulate(rebalance_dates, series, top_k=top_k, capital=args.capital)
            randoms = [simulate_random(rebalance_dates, series, seed, top_k=top_k, capital=args.capital)
                       for seed in range(args.n_seeds)]
            randoms.sort()
            n_beat = sum(1 for r in randoms if r >= actual["final_capital"])
            mean_r = sum(randoms) / len(randoms)
            std_r = (sum((r - mean_r) ** 2 for r in randoms) / len(randoms)) ** 0.5
            z = (actual["final_capital"] - mean_r) / std_r if std_r else float("nan")
            pct = 100 * sum(1 for r in randoms if r < actual["final_capital"]) / len(randoms)
            print(f"top_k={top_k}: actual={actual['final_capital']:.0f} random_mean={mean_r:.0f} "
                  f"random_std={std_r:.0f} percentile={pct:.1f} empirical_p={n_beat/len(randoms):.4f} z={z:.2f}")
    else:
        full_check(top_k=args.top_k, capital=args.capital)
