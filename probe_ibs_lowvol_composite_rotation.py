"""
Probe: composite cross-sectional score (IBS + low-volatility) monthly
rotation — Entry 50.

Two independently-real cross-sectional findings exist in this project:
IBS rotation (Thirty-eighth/Thirty-ninth entries — the project's strongest
result, survives widening and a survivorship stress test) and the
low-volatility anomaly (Twenty-fourth entry — a real academic factor, but
decays hard in the same recent Q4 2024-2026 window that basket-wide
directional bets keep failing in). Two combination shapes were already
tried and both HURT: AND-gating two signals into one trade's entry/exit
(Eleventh/Twenty-second entries) and running independently-real STRATEGIES
as a diversified multi-strategy portfolio (Thirty-second entry — too
correlated for a free lunch). This is a third, different combination
shape, never tested: blend the two signals into a single composite
CROSS-SECTIONAL RANK SCORE (not an AND-filter, not a separate capital
sleeve) and use that one score as the sort key for the same monthly
rotation structure that worked for IBS alone. `vol_weight=0.0` reduces
exactly to the Thirty-eighth entry's pure-IBS rotation (kept as the
baseline column in every sweep below); `vol_weight=1.0` reduces to a
pure low-vol rotation on this same universe/cost model.

Reuses probe_ibs_rotation_widen.WIDE_UNIVERSE/build_wide_price_series (the
Thirty-ninth entry's 52-stock universe, this project's standard for any
cross-sectional claim) and probe_ibs_rotation's avg_ibs/dates_closes_maps/
price_at_or_before/month_end_dates/fetch_calendar/annualized_return_pct —
no fetch/calendar logic is reimplemented, per the Forty-ninth entry's
lesson about that drifting when duplicated.
"""
import argparse
import bisect
import statistics

from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series
from probe_ibs_rotation import (
    avg_ibs, dates_closes_maps, price_at_or_before, month_end_dates,
    fetch_calendar, annualized_return_pct, _report,
)
from probe_ibs_rotation_significance import simulate_random

VOL_LOOKBACK = 20


def trailing_volatility(dates: list, closes: list, as_of, lookback: int = VOL_LOOKBACK):
    """Trailing daily-return stdev ending at-or-before as_of — the
    "calmness" half of the composite score. 20 trading days (~1 month),
    comparable in spirit to the IBS side's 5-day lookback but long enough
    for a stdev estimate to mean something."""
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < lookback:
        return None
    window = closes[idx - lookback: idx + 1]
    rets = [(window[i] - window[i - 1]) / window[i - 1] for i in range(1, len(window))]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets)


def _zscore(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0 for _ in values]
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return [0.0 for _ in values]
    return [(v - mean) / sd for v in values]


def rank_composite(series: dict, dates_map: dict, closes_map: dict, as_of, ibs_lookback: int,
                    vol_lookback: int, top_k: int, vol_weight: float) -> list[tuple[float, str, float]]:
    """Cross-sectional composite score, ascending (lower = more oversold
    AND calmer): (1 - vol_weight) * z(IBS) + vol_weight * z(volatility).
    vol_weight=0.0 is exactly probe_ibs_rotation.rank_by_ibs's ordering
    (verified in tests/test_ibs_lowvol_composite_rotation.py)."""
    raw = []
    for sym, candles in series.items():
        ibs = avg_ibs(candles, dates_map[sym], as_of, ibs_lookback)
        vol = trailing_volatility(dates_map[sym], closes_map[sym], as_of, vol_lookback)
        px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if ibs is None or vol is None or px is None:
            continue
        raw.append((sym, ibs, vol, px))
    if not raw:
        return []
    ibs_z = _zscore([r[1] for r in raw])
    vol_z = _zscore([r[2] for r in raw])
    scored = [((1 - vol_weight) * iz + vol_weight * vz, sym, px)
              for (sym, ibs, vol, px), iz, vz in zip(raw, ibs_z, vol_z)]
    scored.sort(key=lambda t: t[0])
    return scored[:top_k]


def simulate(rebalance_dates: list, series: dict, top_k: int = 5, ibs_lookback: int = 5,
             vol_lookback: int = VOL_LOOKBACK, vol_weight: float = 0.5, cost_pct: float = 0.2,
             dp_charge: float = 16.0, capital: float = 100_000.0):
    dates_map, closes_map = dates_closes_maps(series)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        picks = rank_composite(series, dates_map, closes_map, entry_date, ibs_lookback,
                                vol_lookback, top_k, vol_weight)
        picks = [(score, sym, entry_px, price_at_or_before(dates_map[sym], closes_map[sym], exit_date))
                 for score, sym, entry_px in picks]
        picks = [p for p in picks if p[3] is not None]
        if not picks:
            continue
        notional_each = capital_track / len(picks)
        month_pnl = 0.0
        for _, sym, entry_px, exit_px in picks:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ibs-lookback", type=int, default=5)
    parser.add_argument("--vol-lookback", type=int, default=VOL_LOOKBACK)
    parser.add_argument("--vol-weight", type=float, default=0.5)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    parser.add_argument("--sweep", action="store_true", help="perturbation grid over vol_weight x top_k")
    parser.add_argument("--significance", action="store_true", help="random-control check at current top_k")
    parser.add_argument("--n-seeds", type=int, default=1000)
    args = parser.parse_args()

    series = build_wide_price_series(args.period)
    rebalance_dates = month_end_dates(fetch_calendar(args.period))
    kwargs = dict(top_k=args.top_k, ibs_lookback=args.ibs_lookback, vol_lookback=args.vol_lookback,
                  vol_weight=args.vol_weight, capital=args.capital)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25 if len(rebalance_dates) > 1 else 0.0

    if args.walk_forward:
        cutoff = len(rebalance_dates) // 2
        in_s = simulate(rebalance_dates[:cutoff + 1], series, **kwargs)
        out_s = simulate(rebalance_dates[cutoff:], series, **kwargs)
        _report("In-sample  (first half)", in_s, args.capital, years / 2)
        _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2)
        consistent = (in_s["final_capital"] > args.capital) == (out_s["final_capital"] > args.capital)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    elif args.quarter_split:
        for i, r in enumerate(quarter_split(rebalance_dates, series, **kwargs), start=1):
            if r is None:
                print(f"Q{i}: insufficient data")
                continue
            _report(f"Q{i}", r, args.capital, years / 4)
    elif args.sweep:
        for vw in (0.0, 0.25, 0.5, 0.75, 1.0):
            for tk in (3, 5, 8):
                sweep_kwargs = dict(kwargs, top_k=tk, vol_weight=vw)
                r = simulate(rebalance_dates, series, **sweep_kwargs)
                ann = annualized_return_pct(r["final_capital"], args.capital, years)
                qs = quarter_split(rebalance_dates, series, **sweep_kwargs)
                q_signs = ["+" if (q and q["total_net"] > 0) else "-" for q in qs]
                print(f"vol_weight={vw:.2f} top_k={tk}: annualized={ann:6.2f}%/yr "
                      f"max_dd={r['max_dd']:5.1f}% quarters={''.join(q_signs)}")
    elif args.significance:
        actual = simulate(rebalance_dates, series, **kwargs)
        actual_final = actual["final_capital"]
        randoms = [simulate_random(rebalance_dates, series, seed, top_k=args.top_k, capital=args.capital)
                   for seed in range(args.n_seeds)]
        mean_r = statistics.fmean(randoms)
        std_r = statistics.pstdev(randoms)
        z = (actual_final - mean_r) / std_r if std_r else float("nan")
        percentile = 100 * sum(1 for r in randoms if r < actual_final) / args.n_seeds
        empirical_p = sum(1 for r in randoms if r >= actual_final) / args.n_seeds
        print(f"vol_weight={args.vol_weight} top_k={args.top_k}: actual_final={actual_final:.0f} "
              f"random_mean={mean_r:.0f} random_std={std_r:.0f} percentile={percentile:.1f} "
              f"empirical_p={empirical_p:.4f} z={z:.2f} (n_seeds={args.n_seeds})")
    else:
        result = simulate(rebalance_dates, series, **kwargs)
        _report("Full period", result, args.capital, years)
