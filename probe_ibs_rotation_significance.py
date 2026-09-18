"""
Probe: strengthened random-control significance test for the Thirty-eighth
entry's IBS rotation finding, on the Thirty-ninth entry's widened 52-stock
universe.

The Thirty-eighth entry ran 200 random-5-stock-per-month control seeds and
found the real strategy at the 86.5th percentile (z=1.06) - real but not an
overwhelming statistical outlier on that one test. This probe strengthens
that check: more seeds (1000+, not 200) for a proper empirical p-value, and
a sweep across nearby portfolio sizes (top_k=3, 5, 8) to check the edge
isn't itself a lucky parameter pick.
"""
import argparse
import random

from probe_ibs_rotation_widen import (
    WIDE_UNIVERSE, build_wide_price_series, simulate_wide,
)
from probe_ibs_rotation import month_end_dates, fetch_calendar, price_at_or_before


def simulate_random(rebalance_dates, series, seed, top_k=5,
                     cost_pct=0.2, dp_charge=16.0, capital=100_000.0, slippage_pct=0.0):
    """Same mechanics as simulate_wide but picks top_k RANDOM eligible
    names each month instead of ranking by IBS - the control distribution."""
    rng = random.Random(seed)
    dates_map = {s: [c["date"].date() for c in cds] for s, cds in series.items()}
    closes_map = {s: [c["close"] for c in cds] for s, cds in series.items()}

    capital_track = capital
    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        eligible = []
        for sym, candles in series.items():
            entry_px = price_at_or_before(dates_map[sym], closes_map[sym], entry_date)
            exit_px = price_at_or_before(dates_map[sym], closes_map[sym], exit_date)
            if entry_px is None or exit_px is None:
                continue
            eligible.append((sym, entry_px, exit_px))
        if not eligible:
            continue
        picks = rng.sample(eligible, min(top_k, len(eligible)))
        notional_each = capital_track / len(picks)
        month_pnl = 0.0
        for _, entry_px, exit_px in picks:
            fill_entry = entry_px * (1 + slippage_pct / 100)
            fill_exit = exit_px * (1 - slippage_pct / 100)
            ret = (fill_exit - fill_entry) / fill_entry
            gross = notional_each * ret
            cost = notional_each * (cost_pct / 100) * 2 + dp_charge
            month_pnl += gross - cost
        capital_track += month_pnl
    return capital_track


def run(n_seeds=1000, top_ks=(3, 5, 8), lookback=5, period="10y", capital=100_000.0,
        slippage_pct=0.0):
    series = build_wide_price_series(period)
    rebalance_dates = month_end_dates(fetch_calendar(period))

    for top_k in top_ks:
        actual = simulate_wide(rebalance_dates, series, top_k=top_k, lookback=lookback,
                                capital=capital, slippage_pct=slippage_pct)
        actual_final = actual["final_capital"]

        randoms = [simulate_random(rebalance_dates, series, seed, top_k=top_k, capital=capital,
                                    slippage_pct=slippage_pct)
                   for seed in range(n_seeds)]
        randoms.sort()
        n_beat_actual = sum(1 for r in randoms if r >= actual_final)
        empirical_p = n_beat_actual / n_seeds
        percentile = 100 * sum(1 for r in randoms if r < actual_final) / n_seeds
        mean_r = sum(randoms) / n_seeds
        std_r = (sum((r - mean_r) ** 2 for r in randoms) / n_seeds) ** 0.5
        z = (actual_final - mean_r) / std_r if std_r else float("nan")

        print(f"top_k={top_k}: actual_final={actual_final:.0f}  "
              f"random_mean={mean_r:.0f} random_std={std_r:.0f}  "
              f"percentile={percentile:.1f}  empirical_p={empirical_p:.4f}  z={z:.2f}  "
              f"(n_seeds={n_seeds}, {n_beat_actual} random draws >= actual)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-seeds", type=int, default=1000)
    parser.add_argument("--top-ks", type=int, nargs="+", default=[3, 5, 8])
    parser.add_argument("--slippage-pct", type=float, default=0.0)
    args = parser.parse_args()
    run(n_seeds=args.n_seeds, top_ks=tuple(args.top_ks), slippage_pct=args.slippage_pct)
