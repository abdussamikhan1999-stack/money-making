"""Probe: NIFTYBEES ETF beta hedge on top of long-only IBS rotation
(Forty-sixth entry) — the Forty-fifth entry found that hedging the
long-only strategy's broad market beta via NIFTY FUTURES is mechanically
sound (drawdown 38.2%->25.5%, 8.19%/yr, walk-forward consistent, all 4
quarters positive) but blocked by the same capital-tier wall this
project has already closed out for every other Nifty derivative: one
65-unit futures lot needs ~Rs 196,800 margin, 2-6.5x this project's
Rs 30,000-100,000 target capital.

This substitutes the hedge INSTRUMENT while keeping the exact same
beta-sizing logic: NIFTYBEES.NS (Nippon India ETF Nifty 50 BEES),
confirmed live and listed via https://api.kite.trade/instruments
(NSE, EQ segment, lot_size=1 -- an ordinary equity share, not a fixed
derivative lot). Unlike a futures lot, an ETF share can be shorted/bought
in ANY whole-unit quantity, so the hedge notional can be sized to
whatever capital actually allows, potentially routing around the exact
wall that blocked futures.

Same SLB/short-selling caveat the Forty-fourth/Forty-fifth entries
already flagged for any month-long short in the NSE cash segment applies
here too (not modeled: borrow cost, borrow availability) -- this is a
theoretical hedge-shape check, not a directly tradable construction, same
caveat as every prior long/short attempt in this file.
"""
import argparse
import math
import statistics

from probe_ibs_rotation_widen import build_wide_price_series
from probe_ibs_rotation_longshort import long_only_monthly_returns, nifty_monthly_returns
from probe_ibs_rotation_hedged import compute_beta, NIFTY_FUTURES_MARGIN_PCT  # noqa: F401 (kept for cross-reference)
from probe_ibs_rotation import (
    simulate, dates_closes_maps, rank_by_ibs, month_end_dates, fetch_calendar,
    price_at_or_before, annualized_return_pct, _report,
)

ETF_SYMBOL = "NIFTYBEES.NS"
ETF_COST_PCT = 0.2  # same equity round-trip cost model as every stock leg in this file


def fetch_etf_series(period: str):
    from data_yfinance import fetch_candles
    return fetch_candles(ETF_SYMBOL, "1d", period)


def simulate_etf_hedged(rebalance_dates, series, etf_candles, top_k=5, lookback=5,
                         cost_pct=0.2, dp_charge=16.0, capital=100_000.0,
                         hedge_ratio=1.0, beta=1.0):
    """Long-only IBS rotation (unchanged stock picks) + a short NIFTYBEES
    position sized at hedge_ratio * beta * capital, rounded DOWN to whole
    shares (the whole point of this probe -- no fractional-share hedge)."""
    dates_map, closes_map = dates_closes_maps(series)
    etf_dates = [c["date"].date() for c in etf_candles]
    etf_closes = [c["close"] for c in etf_candles]

    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []
    min_qty_seen = None

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        picks = rank_by_ibs(series, dates_map, closes_map, entry_date, lookback, top_k)
        picks = [(sym, entry_px, price_at_or_before(dates_map[sym], closes_map[sym], exit_date))
                 for _, sym, entry_px in picks]
        picks = [p for p in picks if p[2] is not None]
        stock_pnl = 0.0
        if picks:
            notional_each = capital_track / len(picks)
            for _, entry_px, exit_px in picks:
                ret = (exit_px - entry_px) / entry_px
                gross = notional_each * ret
                cost = notional_each * (cost_pct / 100) * 2 + dp_charge
                stock_pnl += gross - cost

        etf_p0 = price_at_or_before(etf_dates, etf_closes, entry_date)
        etf_p1 = price_at_or_before(etf_dates, etf_closes, exit_date)
        hedge_notional = hedge_ratio * beta * capital_track
        qty = math.floor(hedge_notional / etf_p0) if etf_p0 else 0
        min_qty_seen = qty if min_qty_seen is None else min(min_qty_seen, qty)
        etf_pnl = 0.0
        if qty > 0 and etf_p0 and etf_p1:
            etf_pnl = -qty * (etf_p1 - etf_p0)
            etf_pnl -= qty * etf_p0 * (ETF_COST_PCT / 100) * 2  # round-trip STT+stamp, both legs

        month_pnl = stock_pnl + etf_pnl
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=len(picks), etf_qty=qty))

    total_net = sum(m["pnl"] for m in months)
    wins = sum(1 for m in months if m["pnl"] > 0)
    return dict(months=months, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_months=len(months), win_rate=wins / len(months) if months else 0.0,
                min_qty_seen=min_qty_seen)


def check_tracking(etf_candles, calendar_candles):
    """Sanity check: is NIFTYBEES actually a clean 1:1 proxy for NIFTY over
    this project's backtest window, or does it carry meaningful tracking
    error / a shorter listing history?"""
    etf_dates = [c["date"].date() for c in etf_candles]
    nifty_dates = [c["date"].date() for c in calendar_candles]
    print(f"NIFTYBEES history: {etf_dates[0]} to {etf_dates[-1]} ({len(etf_dates)} bars)")
    print(f"NIFTY calendar:    {nifty_dates[0]} to {nifty_dates[-1]} ({len(nifty_dates)} bars)")
    common_start = max(etf_dates[0], nifty_dates[0])
    if etf_dates[0] > nifty_dates[0]:
        print(f"NOTE: NIFTYBEES history starts AFTER the backtest window's start "
              f"({etf_dates[0]} vs {nifty_dates[0]}) -- hedge overlay only valid from {common_start}")
    etf_closes = [c["close"] for c in etf_candles]
    nifty_closes = [c["close"] for c in calendar_candles]
    idx_etf = [i for i, d in enumerate(etf_dates) if d >= common_start]
    idx_nifty = [i for i, d in enumerate(nifty_dates) if d >= common_start]
    if len(idx_etf) > 1 and len(idx_nifty) > 1:
        etf_ret = (etf_closes[idx_etf[-1]] / etf_closes[idx_etf[0]] - 1) * 100
        nifty_ret = (nifty_closes[idx_nifty[-1]] / nifty_closes[idx_nifty[0]] - 1) * 100
        print(f"Total return over common window: NIFTYBEES {etf_ret:.1f}%  vs  ^NSEI {nifty_ret:.1f}%  "
              f"(tracking gap: {etf_ret - nifty_ret:.1f}pp over the whole period)")
    return common_start


def run(top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    etf_candles = fetch_etf_series(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    check_tracking(etf_candles, calendar_candles)
    print()

    long_only = simulate(rebalance_dates, series, **kwargs)
    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    print(f"Regression beta (long-only monthly returns vs NIFTY monthly returns): {beta:.3f}\n")

    print("=== Long-only baseline (unhedged) ===")
    _report("Full period", long_only, capital, years)

    for label, ratio in [("full beta-hedge (1.0x)", 1.0), ("half beta-hedge (0.5x)", 0.5)]:
        hedged = simulate_etf_hedged(rebalance_dates, series, etf_candles, hedge_ratio=ratio, beta=beta, **kwargs)
        print(f"\n=== ETF-hedged, {label} ===")
        _report("Full period", hedged, capital, years)
        print(f"  smallest monthly ETF share quantity across the whole run: {hedged['min_qty_seen']}")

    etf_p0 = price_at_or_before([c["date"].date() for c in etf_candles],
                                 [c["close"] for c in etf_candles], rebalance_dates[-1])
    print(f"\n--- Capital feasibility (NIFTYBEES @ ~Rs {etf_p0:.2f}/share, no fixed lot size) ---")
    for cap in [30_000.0, 100_000.0]:
        notional = 1.0 * beta * cap
        qty = math.floor(notional / etf_p0)
        print(f"At Rs {cap:,.0f} capital: full-beta hedge notional Rs {notional:,.0f} -> "
              f"{qty} whole shares ({'FEASIBLE' if qty >= 1 else 'NOT FEASIBLE, price too high for this capital'})")


def walk_forward(top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0,
                  hedge_ratio=1.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    etf_candles = fetch_etf_series(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    print(f"beta={beta:.3f}, hedge_ratio={hedge_ratio}\n")

    cutoff = len(rebalance_dates) // 2
    halves = []
    for half_label, half_dates in [("In-sample  (first half)", rebalance_dates[:cutoff + 1]),
                                    ("Out-of-sample (2nd half)", rebalance_dates[cutoff:])]:
        hedged = simulate_etf_hedged(half_dates, series, etf_candles, hedge_ratio=hedge_ratio, beta=beta, **kwargs)
        _report(half_label, hedged, capital, years / 2)
        halves.append(hedged)
    return halves


def quarter_split(top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0,
                   hedge_ratio=1.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    etf_candles = fetch_etf_series(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    print(f"beta={beta:.3f}, hedge_ratio={hedge_ratio}\n")

    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    results = []
    for i in range(4):
        chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
        r = simulate_etf_hedged(chunk, series, etf_candles, hedge_ratio=hedge_ratio, beta=beta, **kwargs)
        results.append(r)
        _report(f"Q{i+1}", r, capital, years / 4)
    print(f"all 4 quarters positive: {all(r['total_net'] > 0 for r in results)}")
    return results


def ratio_sweep(ratios, top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0):
    """Forty-seventh entry: sweep the hedge ratio on a finer grid than the
    Forty-sixth entry's single 0/0.5/1.0 test, checking 0.50 isn't a lucky
    single point the way SuperTrend's st_period default was."""
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    etf_candles = fetch_etf_series(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    print(f"beta={beta:.3f}\n")
    print(f"{'ratio':>6} {'return%/yr':>11} {'maxDD%':>8} {'WF halves (in/out)':>22} {'consistent':>11} {'quarters+':>10}")

    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    cutoff = n // 2
    rows = []
    for ratio in ratios:
        full = simulate_etf_hedged(rebalance_dates, series, etf_candles, hedge_ratio=ratio, beta=beta, **kwargs)
        ann = annualized_return_pct(full["final_capital"], capital, years)

        in_s = simulate_etf_hedged(rebalance_dates[:cutoff + 1], series, etf_candles, hedge_ratio=ratio, beta=beta, **kwargs)
        out_s = simulate_etf_hedged(rebalance_dates[cutoff:], series, etf_candles, hedge_ratio=ratio, beta=beta, **kwargs)
        ann_in = annualized_return_pct(in_s["final_capital"], capital, years / 2)
        ann_out = annualized_return_pct(out_s["final_capital"], capital, years / 2)
        consistent = in_s["total_net"] > 0 and out_s["total_net"] > 0

        quarters = []
        for i in range(4):
            chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
            r = simulate_etf_hedged(chunk, series, etf_candles, hedge_ratio=ratio, beta=beta, **kwargs)
            quarters.append(r["total_net"] > 0)
        n_pos_q = sum(quarters)

        print(f"{ratio:>6.3f} {ann:>10.2f}% {full['max_dd']:>7.1f}% "
              f"{ann_in:>+8.2f}/{ann_out:>+8.2f}% {str(consistent):>11} {n_pos_q:>8}/4")
        rows.append(dict(ratio=ratio, ann=ann, max_dd=full["max_dd"], ann_in=ann_in, ann_out=ann_out,
                          consistent=consistent, n_pos_q=n_pos_q))
    return rows


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
    parser.add_argument("--hedge-ratio", type=float, default=1.0)
    parser.add_argument("--ratio-sweep", action="store_true")
    args = parser.parse_args()

    kwargs = dict(top_k=args.top_k, lookback=args.lookback, capital=args.capital,
                  cost_pct=args.cost_pct, dp_charge=args.dp_charge, period=args.period)

    if args.ratio_sweep:
        ratio_sweep([0.25, 0.375, 0.50, 0.625, 0.75], **kwargs)
    elif args.walk_forward:
        walk_forward(hedge_ratio=args.hedge_ratio, **kwargs)
    elif args.quarter_split:
        quarter_split(hedge_ratio=args.hedge_ratio, **kwargs)
    else:
        run(**kwargs)
