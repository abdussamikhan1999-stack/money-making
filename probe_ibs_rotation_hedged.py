"""Probe: NIFTY-futures beta hedge on top of long-only IBS rotation
(Forty-fifth entry) — the Forty-fourth entry's per-stock short leg fought
IBS's real, one-sided long/oversold edge and destroyed almost the entire
return. This tests a more targeted hedge instead: leave every individual
stock pick untouched (100% of the stock-picking edge intact) and only
offset the portfolio's BROAD MARKET BETA with a single short NIFTY
futures position, sized via a real OLS regression of the long-only
strategy's own monthly returns against NIFTY's.

NIFTY futures are a real, currently-listed Kite instrument (confirmed live
via https://api.kite.trade/instruments, the Twenty-ninth entry's
no-auth-needed instrument master: NIFTY26SEPFUT/OCT/NOV, lot_size=65,
NFO-FUT segment) — so, unlike a per-stock short, this doesn't hit the
SLB/short-selling-not-modeled caveat the Forty-fourth entry had to flag.
But index futures carry their own real constraint: a single lot's notional
at current NIFTY levels (~23,300 x 65 ~ Rs 1.5M) needs roughly 12-15%
margin as collateral (~Rs 1.8-2.3L) -- this project's own Sixth/Seventh/
Ninth entries already established this exact "Rs 1.5-2L per lot" figure
for Nifty futures/options margin, closing out low-capital access to any
Nifty derivative at this project's Rs 30,000-100,000 target range. This
probe checks that wall directly against the fetched lot size before
trusting any backtest number as actionable.
"""
import argparse
import statistics

from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series
from probe_ibs_rotation_longshort import long_only_monthly_returns, nifty_monthly_returns
from probe_ibs_rotation import (
    simulate, dates_closes_maps, rank_by_ibs, month_end_dates, fetch_calendar,
    price_at_or_before, annualized_return_pct, _report,
)

NIFTY_LOT_SIZE = 65  # confirmed live via api.kite.trade/instruments, 2026-09
NIFTY_FUTURES_MARGIN_PCT = 0.13  # SPAN+exposure, typical for index futures; informational
FUTURES_COST_PCT = 0.05  # round-trip, lower than equity delivery's 0.2% (Sixth entry's own figure)


def compute_beta(lo_rets: list[float], nifty_rets: list[float]) -> float:
    n = min(len(lo_rets), len(nifty_rets))
    slope, _intercept = statistics.linear_regression(nifty_rets[:n], lo_rets[:n])
    return slope


def simulate_hedged(rebalance_dates, series, calendar_candles, top_k=5, lookback=5,
                     cost_pct=0.2, dp_charge=16.0, capital=100_000.0, hedge_ratio=1.0,
                     beta=1.0):
    """Long-only IBS rotation (unchanged stock picks) + a short NIFTY-notional
    overlay sized at hedge_ratio * beta * capital, marked to market each
    month against NIFTY's own return. Notional-based (a real account would
    round to whole 65-unit lots; that rounding is checked separately in
    lot_feasibility(), not applied here so the beta-hedge shape itself is
    visible without lot-quantization noise)."""
    dates_map, closes_map = dates_closes_maps(series)
    cal_dates = [c["date"].date() for c in calendar_candles]
    cal_closes = [c["close"] for c in calendar_candles]

    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []

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

        nifty_p0 = price_at_or_before(cal_dates, cal_closes, entry_date)
        nifty_p1 = price_at_or_before(cal_dates, cal_closes, exit_date)
        nifty_ret = (nifty_p1 - nifty_p0) / nifty_p0 if nifty_p0 else 0.0
        hedge_notional = hedge_ratio * beta * capital_track
        futures_pnl = -hedge_notional * nifty_ret
        futures_cost = abs(hedge_notional) * (FUTURES_COST_PCT / 100)

        month_pnl = stock_pnl + futures_pnl - futures_cost
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=len(picks)))

    total_net = sum(m["pnl"] for m in months)
    wins = sum(1 for m in months if m["pnl"] > 0)
    return dict(months=months, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_months=len(months), win_rate=wins / len(months) if months else 0.0)


def lot_feasibility(spot: float, capital: float, hedge_notional: float) -> dict:
    lot_notional = NIFTY_LOT_SIZE * spot
    lots_needed = hedge_notional / lot_notional if lot_notional else 0.0
    margin_for_one_lot = lot_notional * NIFTY_FUTURES_MARGIN_PCT
    return dict(spot=spot, lot_notional=lot_notional, lots_needed=lots_needed,
                margin_for_one_lot=margin_for_one_lot,
                one_lot_feasible_at_capital=margin_for_one_lot <= capital)


def run(top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    long_only = simulate(rebalance_dates, series, **kwargs)
    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    corr = statistics.correlation(lo_rets[:len(nifty_rets)], nifty_rets[:len(lo_rets)])
    print(f"Regression beta (long-only monthly returns vs NIFTY monthly returns): {beta:.3f}")
    print(f"(correlation, for cross-check against the Forty-fourth entry's 0.796: {corr:.3f})\n")

    print("=== Long-only baseline ===")
    _report("Full period", long_only, capital, years)

    results = {"long_only": long_only, "beta": beta}
    for label, ratio in [("full beta-hedge (1.0x)", 1.0), ("half beta-hedge (0.5x)", 0.5)]:
        hedged = simulate_hedged(rebalance_dates, series, calendar_candles, hedge_ratio=ratio, beta=beta, **kwargs)
        h_rets = [m["pnl"] / capital for m in hedged["months"]]  # approx, for correlation read only
        print(f"=== Hedged, {label} ===")
        _report("Full period", hedged, capital, years)
        results[f"hedged_{ratio}"] = hedged

    spot = price_at_or_before([c["date"].date() for c in calendar_candles],
                               [c["close"] for c in calendar_candles], rebalance_dates[-1])
    hedge_notional_full = 1.0 * beta * capital
    feas = lot_feasibility(spot, capital, hedge_notional_full)
    print(f"\n--- Lot/capital feasibility check (NIFTY futures, lot_size={NIFTY_LOT_SIZE}, "
          f"live spot~{spot:.0f}) ---")
    print(f"One lot's notional: Rs {feas['lot_notional']:,.0f}; "
          f"margin for ONE lot (~{NIFTY_FUTURES_MARGIN_PCT*100:.0f}%): Rs {feas['margin_for_one_lot']:,.0f}")
    print(f"At Rs {capital:,.0f} capital, hedge would need {feas['lots_needed']:.2f} lots "
          f"(fractional lots aren't tradable); one whole lot's margin alone is "
          f"{'WITHIN' if feas['one_lot_feasible_at_capital'] else 'ABOVE'} this capital.")
    return results


def walk_forward(top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    print(f"beta={beta:.3f}\n")

    cutoff = len(rebalance_dates) // 2
    for half_label, half_dates in [("In-sample  (first half)", rebalance_dates[:cutoff + 1]),
                                    ("Out-of-sample (2nd half)", rebalance_dates[cutoff:])]:
        in_s = simulate_hedged(half_dates, series, calendar_candles, hedge_ratio=1.0, beta=beta, **kwargs)
        _report(half_label, in_s, capital, years / 2)


def quarter_split(top_k=5, lookback=5, period="10y", capital=100_000.0, cost_pct=0.2, dp_charge=16.0):
    series = build_wide_price_series(period)
    calendar_candles = fetch_calendar(period)
    rebalance_dates = month_end_dates(calendar_candles)
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, cost_pct=cost_pct, dp_charge=dp_charge)

    lo_rets = long_only_monthly_returns(rebalance_dates, series, top_k, lookback, cost_pct, dp_charge, capital)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    beta = compute_beta(lo_rets, nifty_rets)
    print(f"beta={beta:.3f}\n")

    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    results = []
    for i in range(4):
        chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
        r = simulate_hedged(chunk, series, calendar_candles, hedge_ratio=1.0, beta=beta, **kwargs)
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
        run(**kwargs)
