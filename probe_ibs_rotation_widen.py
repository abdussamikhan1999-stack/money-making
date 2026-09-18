"""
Probe: widen-and-check for the Thirty-eighth entry's monthly cross-sectional
IBS rotation finding (`probe_ibs_rotation.py`).

That entry screened the 40-stock `probe_pead.py` UNIVERSE and found the
strongest result in this project's history (15.51%/yr, all 4 quarters
positive across 5 parameter configs, 16/16 perturbation cells clean,
75% of names individually net-positive) — but per this project's own
established practice (PEAD's Thirtieth/Thirty-first entries: clean at 20
stocks, fell apart at 40; 52-week-high's Thirty-fifth/Thirty-sixth: same
pattern), any promising cross-sectional result must be widened before it
counts as corroborated rather than a universe-specific artifact.

Widens the 40-stock UNIVERSE with the Thirty-third entry's 12-stock
small/mid-cap universe (a genuinely different market-cap segment never
combined with the large-cap universe before) to 52 names, and reruns
walk-forward + quarter-split + attribution on the combined set. Reuses
probe_ibs_rotation.py's simulate()/build_price_series()/fetch_calendar()
unchanged - this probe adds no new strategy logic, only a wider symbol
list and a driver loop, consistent with probe_high52w_widen.py's own
"thin driver, no new unit tests" precedent.
"""
import argparse
import statistics

from probe_pead import UNIVERSE as LARGE_CAP_UNIVERSE
from probe_ibs_rotation import (
    avg_ibs, build_price_series, fetch_calendar, month_end_dates,
    price_at_or_before, quarter_split, simulate, annualized_return_pct, _report,
)

# Thirty-third entry's small/mid-cap universe, never combined with the
# large-cap universe before this entry.
SMALL_MID_CAP_UNIVERSE = [
    "DEEPAKNTR.NS", "CROMPTON.NS", "RADICO.NS", "APLAPOLLO.NS",
    "JUBLPHARMA.NS", "SYMPHONY.NS", "VGUARD.NS", "RATNAMANI.NS",
    "PERSISTENT.NS", "GRAPHITE.NS", "ELGIEQUIP.NS", "KAJARIACER.NS",
]

WIDE_UNIVERSE = LARGE_CAP_UNIVERSE + SMALL_MID_CAP_UNIVERSE


def build_wide_price_series(period: str, retries: int = 3):
    """Same retry-on-fetch-failure logic as probe_ibs_rotation.build_price_series,
    but over WIDE_UNIVERSE instead of the hardcoded probe_pead UNIVERSE import."""
    from data_yfinance import fetch_candles
    series = {}
    for sym in WIDE_UNIVERSE:
        candles = []
        for _ in range(retries):
            candles = fetch_candles(sym, "1d", period)
            if candles:
                break
        if candles:
            series[sym] = candles
        else:
            print(f"{sym}: fetch failed after {retries} retries, excluded")
    return series


def simulate_wide(rebalance_dates, series, top_k=5, lookback=5,
                   cost_pct=0.2, dp_charge=16.0, capital=100_000.0, track_picks=False,
                   slippage_pct=0.0, whole_shares=False):
    """Same mechanics as probe_ibs_rotation.simulate() (including its
    whole_shares option, see that function's docstring), extended to
    optionally track which symbol was picked each month (for attribution)."""
    dates_map = {s: [c["date"].date() for c in cds] for s, cds in series.items()}
    closes_map = {s: [c["close"] for c in cds] for s, cds in series.items()}

    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []
    per_symbol_pnl = {}

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        scores = []
        for sym, candles in series.items():
            score = avg_ibs(candles, dates_map[sym], entry_date, lookback)
            entry_px = price_at_or_before(dates_map[sym], closes_map[sym], entry_date)
            exit_px = price_at_or_before(dates_map[sym], closes_map[sym], exit_date)
            if score is None or entry_px is None or exit_px is None:
                continue
            scores.append((score, sym, entry_px, exit_px))
        scores.sort(key=lambda t: t[0])
        picks = scores[:top_k]
        if not picks:
            continue
        notional_each = capital_track / len(picks)
        month_pnl = 0.0
        active = 0
        for _, sym, entry_px, exit_px in picks:
            fill_entry = entry_px * (1 + slippage_pct / 100)
            fill_exit = exit_px * (1 - slippage_pct / 100)
            if whole_shares:
                shares = int(notional_each // fill_entry)
                if shares == 0:
                    continue
                notional = shares * fill_entry
            else:
                notional = notional_each
            ret = (fill_exit - fill_entry) / fill_entry
            gross = notional * ret
            cost = notional * (cost_pct / 100) * 2 + dp_charge
            trade_pnl = gross - cost
            month_pnl += trade_pnl
            active += 1
            if track_picks:
                per_symbol_pnl[sym] = per_symbol_pnl.get(sym, 0.0) + trade_pnl
        if active == 0:
            continue
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=active))

    total_net = sum(m["pnl"] for m in months)
    wins = sum(1 for m in months if m["pnl"] > 0)
    result = dict(months=months, final_capital=capital_track, total_net=total_net,
                  max_dd=max_dd, n_months=len(months), win_rate=wins / len(months) if months else 0.0)
    if track_picks:
        result["per_symbol_pnl"] = per_symbol_pnl
    return result


def widen_check(top_k=5, lookback=5, period="10y", capital=100_000.0, slippage_pct=0.0,
                 whole_shares=False):
    print(f"Widened universe: {len(WIDE_UNIVERSE)} stocks "
          f"({len(LARGE_CAP_UNIVERSE)} large-cap + {len(SMALL_MID_CAP_UNIVERSE)} small/mid-cap)")
    series = build_wide_price_series(period)
    rebalance_dates = month_end_dates(fetch_calendar(period))
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25

    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital, slippage_pct=slippage_pct,
                  whole_shares=whole_shares)

    # Walk-forward
    cutoff = len(rebalance_dates) // 2
    in_s = simulate_wide(rebalance_dates[:cutoff + 1], series, **kwargs)
    out_s = simulate_wide(rebalance_dates[cutoff:], series, **kwargs)
    _report("In-sample  (first half)", in_s, capital, years / 2)
    _report("Out-of-sample (2nd half)", out_s, capital, years / 2)
    consistent = (in_s["final_capital"] > capital) == (out_s["final_capital"] > capital)
    print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves\n")

    # Quarter-split
    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    quarter_pnls = []
    for i in range(4):
        chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
        r = simulate_wide(chunk, series, **kwargs)
        quarter_pnls.append(r["total_net"])
        _report(f"Q{i+1}", r, capital, years / 4)
    print(f"all 4 quarters positive: {all(p > 0 for p in quarter_pnls)}\n")

    # Full-period attribution
    full = simulate_wide(rebalance_dates, series, track_picks=True, **kwargs)
    _report("Full period", full, capital, years)
    per_sym = full["per_symbol_pnl"]
    n_contributors = len(per_sym)
    n_positive = sum(1 for v in per_sym.values() if v > 0)
    ranked = sorted(per_sym.items(), key=lambda kv: -kv[1])
    top3_share = sum(v for _, v in ranked[:3]) / full["total_net"] * 100 if full["total_net"] else float("nan")
    print(f"\n{n_contributors}/{len(WIDE_UNIVERSE)} stocks selected at least once; "
          f"{n_positive}/{n_contributors} ({n_positive/n_contributors*100:.0f}%) individually net-positive")
    print(f"top-3 contributors' share of total net P&L: {top3_share:.1f}%")
    print("top 5:", [(s, round(v, 0)) for s, v in ranked[:5]])
    print("bottom 5:", [(s, round(v, 0)) for s, v in ranked[-5:]])

    return dict(walk_forward=(in_s, out_s, consistent), quarter_pnls=quarter_pnls,
                full=full, per_symbol_pnl=per_sym)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--lookback", type=int, default=5)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--slippage-pct", type=float, default=0.0)
    parser.add_argument("--whole-shares", action="store_true")
    args = parser.parse_args()
    widen_check(top_k=args.top_k, lookback=args.lookback, capital=args.capital,
                slippage_pct=args.slippage_pct, whole_shares=args.whole_shares)
