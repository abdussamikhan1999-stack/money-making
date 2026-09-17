"""
Probe: long/short IBS rotation (Forty-fourth entry) — does adding a SHORT
leg (short the most-overbought names by the same trailing IBS rank) to the
Thirty-eighth/Thirty-ninth entries' long-only monthly IBS rotation cut
drawdown/NIFTY-beta by hedging out broad-market moves? A meaningful chunk
of the long-only version's 38.2% max drawdown is plausibly just NIFTY beta
(the whole long basket falls in a market-wide selloff), not a flaw in the
IBS ranking itself — this tests whether shorting the mirror-image
overbought names actually hedges that out, or whether the "overbought"
side just isn't a real mirror-image signal (IBS's published edge is
specifically the long/oversold side, per the Thirteenth entry).

REAL-WORLD CAVEAT, load-bearing, not cosmetic: NSE cash-equity short
selling cannot be held overnight — plain delivery shorting must be squared
off the SAME DAY. A month-long short position, as modeled here, is not
directly executable in the cash market at all; it would need Securities
Lending & Borrowing (SLB, its own approval/eligibility/cost process, not
modeled) or single-stock futures (which reintroduce this project's own
already-established fixed-lot capital-tier wall — see the
Twenty-fifth/Twenty-sixth entries). The short leg below is costed with the
same symmetric round-trip cost model as the long leg for a clean
apples-to-apples comparison, but that is a simplification: no SLB borrow
fee, no borrow-availability constraint. Read every long/short number here
as a THEORETICAL hedge-shape check, not a directly tradable retail
strategy the way this project's long-only numbers are.
"""
import argparse
import statistics

from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series
from probe_ibs_rotation import (
    simulate, dates_closes_maps, month_end_dates, fetch_calendar,
    price_at_or_before, avg_ibs, annualized_return_pct, _report,
)


def rank_both(series, dates_map, closes_map, as_of, lookback, top_k):
    """Both ends of the same IBS ranking: most-oversold-first (longs) and
    most-overbought-first (shorts) — mirrors rank_by_ibs's ascending sort."""
    scored = []
    for sym, candles in series.items():
        score = avg_ibs(candles, dates_map[sym], as_of, lookback)
        px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if score is None or px is None:
            continue
        scored.append((score, sym, px))
    scored.sort(key=lambda t: t[0])
    longs = scored[:top_k]
    shorts = list(reversed(scored[-top_k:])) if len(scored) >= top_k else []
    return longs, shorts


def _leg_pnl(picks, side, leg_capital, dates_map, closes_map, exit_date, cost_pct, dp_charge):
    resolved = [(sym, entry_px, price_at_or_before(dates_map[sym], closes_map[sym], exit_date))
                for _, sym, entry_px in picks]
    resolved = [p for p in resolved if p[2] is not None]
    if not resolved:
        return 0.0
    notional_each = leg_capital / len(resolved)
    pnl = 0.0
    for _, entry_px, exit_px in resolved:
        ret = (exit_px - entry_px) / entry_px
        gross = notional_each * ret * side
        cost = notional_each * (cost_pct / 100) * 2 + dp_charge
        pnl += gross - cost
    return pnl


def simulate_longshort(rebalance_dates, series, top_k=5, lookback=5,
                        cost_pct=0.2, dp_charge=16.0, capital=100_000.0):
    """Dollar-neutral: half capital long the most-oversold top_k, half
    capital short the most-overbought top_k, same IBS ranking each month."""
    dates_map, closes_map = dates_closes_maps(series)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        longs, shorts = rank_both(series, dates_map, closes_map, entry_date, lookback, top_k)
        half = capital_track / 2
        long_pnl = _leg_pnl(longs, +1, half, dates_map, closes_map, exit_date, cost_pct, dp_charge)
        short_pnl = _leg_pnl(shorts, -1, half, dates_map, closes_map, exit_date, cost_pct, dp_charge)
        month_pnl = long_pnl + short_pnl
        prior_capital = capital_track
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=len(longs) + len(shorts),
                            ret=month_pnl / prior_capital if prior_capital else 0.0))

    total_net = sum(m["pnl"] for m in months)
    wins = sum(1 for m in months if m["pnl"] > 0)
    return dict(months=months, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_months=len(months), win_rate=wins / len(months) if months else 0.0)


def monthly_returns(result: dict) -> list[float]:
    return [m["ret"] for m in result["months"]]


def nifty_monthly_returns(rebalance_dates, calendar_candles) -> list[float]:
    dates = [c["date"].date() for c in calendar_candles]
    closes = [c["close"] for c in calendar_candles]
    rets = []
    for i in range(len(rebalance_dates) - 1):
        p0 = price_at_or_before(dates, closes, rebalance_dates[i])
        p1 = price_at_or_before(dates, closes, rebalance_dates[i + 1])
        rets.append((p1 - p0) / p0 if p0 else 0.0)
    return rets


def long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital):
    """Re-derives per-month returns for the long-only baseline (reusing
    probe_ibs_rotation.simulate's mechanics) so both series share the exact
    same data fetch/rebalance dates for a clean correlation comparison."""
    dates_map, closes_map = dates_closes_maps(series)
    capital_track = capital
    rets = []
    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        longs, _ = rank_both(series, dates_map, closes_map, entry_date, lookback, top_k)
        pnl = _leg_pnl(longs, +1, capital_track, dates_map, closes_map, exit_date, cost_pct, dp_charge)
        prior = capital_track
        capital_track += pnl
        rets.append(pnl / prior if prior else 0.0)
    return rets


def compare(top_k=5, lookback=5, period="10y", capital=100_000.0,
            cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25

    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital,
                  cost_pct=cost_pct, dp_charge=dp_charge)

    long_only = simulate(rebalance_dates, series, **kwargs)
    long_short = simulate_longshort(rebalance_dates, series, **kwargs)

    print(f"=== Long-only (baseline, Thirty-ninth entry's construction) ===")
    _report("Full period", long_only, capital, years)
    print(f"=== Long/short (this entry) ===")
    _report("Full period", long_short, capital, years)

    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    ls_rets = monthly_returns(long_short)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    n = min(len(lo_rets), len(ls_rets), len(nifty_rets))
    corr_lo = statistics.correlation(lo_rets[:n], nifty_rets[:n])
    corr_ls = statistics.correlation(ls_rets[:n], nifty_rets[:n])

    print(f"\nMax drawdown: long-only={long_only['max_dd']:.1f}%  long/short={long_short['max_dd']:.1f}%")
    print(f"NIFTY monthly-return correlation: long-only={corr_lo:.3f}  long/short={corr_ls:.3f}")

    return dict(long_only=long_only, long_short=long_short, corr_lo=corr_lo, corr_ls=corr_ls)


def walk_forward(top_k=5, lookback=5, period="10y", capital=100_000.0,
                  cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    rebalance_dates = month_end_dates(fetch_calendar(period))
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital,
                  cost_pct=cost_pct, dp_charge=dp_charge)
    cutoff = len(rebalance_dates) // 2
    in_s = simulate_longshort(rebalance_dates[:cutoff + 1], series, **kwargs)
    out_s = simulate_longshort(rebalance_dates[cutoff:], series, **kwargs)
    _report("In-sample  (first half)", in_s, capital, years / 2)
    _report("Out-of-sample (2nd half)", out_s, capital, years / 2)
    consistent = (in_s["final_capital"] > capital) == (out_s["final_capital"] > capital)
    print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    return in_s, out_s, consistent


def quarter_split(top_k=5, lookback=5, period="10y", capital=100_000.0,
                   cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    rebalance_dates = month_end_dates(fetch_calendar(period))
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital,
                  cost_pct=cost_pct, dp_charge=dp_charge)
    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    results = []
    for i in range(4):
        chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
        r = simulate_longshort(chunk, series, **kwargs)
        results.append(r)
        _report(f"Q{i+1}", r, capital, years / 4)
    print(f"all 4 quarters positive: {all(r['total_net'] > 0 for r in results)}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--lookback", type=int, default=5)
    parser.add_argument("--cost-pct", type=float, default=0.2)
    parser.add_argument("--dp-charge", type=float, default=16.0)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    kwargs = dict(top_k=args.top_k, lookback=args.lookback, capital=args.capital,
                  cost_pct=args.cost_pct, dp_charge=args.dp_charge, period=args.period)

    if args.walk_forward:
        walk_forward(**kwargs)
    elif args.quarter_split:
        quarter_split(**kwargs)
    else:
        compare(**kwargs)
