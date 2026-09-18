"""Probe: monthly cross-sectional Amihud (2002) illiquidity rotation --
a genuinely different KIND of mechanism from every candidate this project
has tried so far (see CLAUDE.md's numbered entries): not momentum, not
mean-reversion (IBS/RSI-2), not a volatility factor (low-vol anomaly), not
volume-CONFIRMATION (CMF+OBV) -- this is a LIQUIDITY-RISK-PREMIUM factor.
Amihud, "Illiquidity and stock returns: cross-section and time-series
effects" (Journal of Financial Markets, 2002): stocks with higher
price-impact-per-rupee-of-volume are harder to trade without moving the
price, and investors demand a return premium for bearing that illiquidity
risk. Rank a stock universe each month-end by trailing ILLIQ (mean daily
|return| / dollar volume) and go LONG the most ILLIQUID names -- the
opposite ranking direction from IBS/RSI-2 rotation (which go long the most
OVERSOLD), since here "more extreme" means "more compensated for
illiquidity," not "more likely to mean-revert."

Reuses the exact monthly cross-sectional rotation scaffolding entries
38-53 already built and validated (`probe_ibs_rotation.py` /
`probe_ibs_rotation_widen.py`): `WIDE_UNIVERSE`, `build_wide_price_series`
(candle dicts already carry `volume` -- no new fetch pathway needed, the
Sixteenth entry's CMF/OBV work already established that `data_yfinance.
fetch_candles` returns volume for every symbol it can fetch), `fetch_calendar`,
`month_end_dates`, `price_at_or_before`, and the same STT+stamp+DP+slippage
cost model (`slippage_pct`, added in the Fifty-second entry). Only the
ranking function (`avg_illiq`/`rank_by_illiq`) and `simulate()`'s score
computation are new; everything else is imported unchanged so the cost
model, universe, and calendar can never silently drift from what entries
38-53 already validated.

`WIDE_UNIVERSE` contains no index symbols (`^NSEI` is used only as the
calendar via `fetch_calendar`, never as a tradable pick), so the
Sixteenth entry's "yfinance reports zero volume for an index" caveat
doesn't reduce the eligible universe here the way it did for CMF/OBV --
but `indicators.amihud_illiq()` still handles a zero-volume day inside
any stock's own window defensively (skip that day, same convention as
`chaikin_money_flow`), in case of a genuine no-trade day for a
real stock.
"""
import argparse
import bisect

from indicators import amihud_illiq
from probe_ibs_rotation import (
    fetch_calendar, month_end_dates, price_at_or_before, dates_closes_maps,
    _report,
)
from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series


def avg_illiq(candles: list[dict], dates: list, as_of, lookback: int) -> float | None:
    """Trailing `lookback`-day Amihud ILLIQ as of a given date -- needs
    `lookback + 1` candles up to and including `as_of` (each day's return
    needs the PRIOR day's close, same "+1" requirement `avg_ibs` in
    `probe_ibs_rotation.py` does NOT have, since IBS is a same-day
    positional signal and ILLIQ is a day-over-day return signal)."""
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < lookback:
        return None
    window = candles[idx - lookback: idx + 1]
    return amihud_illiq(window, period=lookback)


def rank_by_illiq(series: dict, dates_map: dict, closes_map: dict, as_of, lookback: int,
                   top_k: int) -> list[tuple[float, str, float]]:
    """Most-ILLIQUID-first ranking (descending ILLIQ) as of a given date --
    the opposite sort direction from `probe_ibs_rotation.rank_by_ibs`
    (ascending, most-oversold-first), since the Amihud premium is
    compensation for HIGH illiquidity, not a mean-reversion signal on a
    low value. Each entry (score, symbol, price_at_or_before(as_of))."""
    scored = []
    for sym, candles in series.items():
        score = avg_illiq(candles, dates_map[sym], as_of, lookback)
        px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if score is None or px is None:
            continue
        scored.append((score, sym, px))
    scored.sort(key=lambda t: -t[0])
    return scored[:top_k]


def simulate(rebalance_dates: list, series: dict, top_k: int = 5, lookback: int = 21,
             cost_pct: float = 0.2, dp_charge: float = 16.0, capital: float = 100_000.0,
             slippage_pct: float = 0.0, track_picks: bool = False):
    """Same mechanics as `probe_ibs_rotation.simulate()`/`probe_ibs_rotation_
    widen.simulate_wide()` (STT+stamp+DP+slippage cost model, equal-weight
    top_k, monthly rebalance) with the ranking swapped for ILLIQ (descending,
    long the most illiquid) instead of IBS (ascending, long the most
    oversold)."""
    dates_map, closes_map = dates_closes_maps(series)

    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []
    per_symbol_pnl = {}

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        picks = rank_by_illiq(series, dates_map, closes_map, entry_date, lookback, top_k)
        picks = [(score, sym, entry_px, price_at_or_before(dates_map[sym], closes_map[sym], exit_date))
                 for score, sym, entry_px in picks]
        picks = [p for p in picks if p[3] is not None]
        if not picks:
            continue
        notional_each = capital_track / len(picks)
        month_pnl = 0.0
        for _, sym, entry_px, exit_px in picks:
            fill_entry = entry_px * (1 + slippage_pct / 100)
            fill_exit = exit_px * (1 - slippage_pct / 100)
            ret = (fill_exit - fill_entry) / fill_entry
            gross = notional_each * ret
            cost = notional_each * (cost_pct / 100) * 2 + dp_charge
            trade_pnl = gross - cost
            month_pnl += trade_pnl
            if track_picks:
                per_symbol_pnl[sym] = per_symbol_pnl.get(sym, 0.0) + trade_pnl
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=len(picks)))

    total_net = sum(m["pnl"] for m in months)
    wins = sum(1 for m in months if m["pnl"] > 0)
    result = dict(months=months, final_capital=capital_track, total_net=total_net,
                  max_dd=max_dd, n_months=len(months), win_rate=wins / len(months) if months else 0.0)
    if track_picks:
        result["per_symbol_pnl"] = per_symbol_pnl
    return result


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
    parser.add_argument("--lookback", type=int, default=21)
    parser.add_argument("--cost-pct", type=float, default=0.2)
    parser.add_argument("--dp-charge", type=float, default=16.0)
    parser.add_argument("--slippage-pct", type=float, default=0.0)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    parser.add_argument("--attribution", action="store_true")
    args = parser.parse_args()

    print(f"Universe: {len(WIDE_UNIVERSE)} stocks")
    series = build_wide_price_series(args.period)
    rebalance_dates = month_end_dates(fetch_calendar(args.period))
    kwargs = dict(top_k=args.top_k, lookback=args.lookback, capital=args.capital,
                  cost_pct=args.cost_pct, dp_charge=args.dp_charge, slippage_pct=args.slippage_pct)
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
        pnls = []
        for i, r in enumerate(quarter_split(rebalance_dates, series, **kwargs), start=1):
            if r is None:
                print(f"Q{i}: insufficient data")
                continue
            pnls.append(r["total_net"])
            _report(f"Q{i}", r, args.capital, years / 4)
        print(f"all 4 quarters positive: {all(p > 0 for p in pnls)}")
    elif args.attribution:
        result = simulate(rebalance_dates, series, track_picks=True, **kwargs)
        _report("Full period", result, args.capital, years)
        per_sym = result["per_symbol_pnl"]
        n_contributors = len(per_sym)
        n_positive = sum(1 for v in per_sym.values() if v > 0)
        ranked = sorted(per_sym.items(), key=lambda kv: -kv[1])
        top3_share = sum(v for _, v in ranked[:3]) / result["total_net"] * 100 if result["total_net"] else float("nan")
        print(f"{n_contributors}/{len(WIDE_UNIVERSE)} stocks selected at least once; "
              f"{n_positive}/{n_contributors} ({n_positive/n_contributors*100:.0f}%) individually net-positive")
        print(f"top-3 contributors' share of total net P&L: {top3_share:.1f}%")
        print("top 5:", [(s, round(v, 0)) for s, v in ranked[:5]])
        print("bottom 5:", [(s, round(v, 0)) for s, v in ranked[-5:]])
    else:
        result = simulate(rebalance_dates, series, **kwargs)
        _report("Full period", result, args.capital, years)
