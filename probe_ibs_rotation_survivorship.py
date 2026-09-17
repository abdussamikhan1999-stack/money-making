"""Probe: survivorship-bias stress test for the Thirty-eighth/Thirty-ninth
entries' monthly cross-sectional IBS rotation finding.

`UNIVERSE` (probe_pead.py) and `SMALL_MID_CAP_UNIVERSE`
(probe_ibs_rotation_widen.py) are both hand-picked using TODAY's
well-known large/mid-caps -- a stock that delisted, went bankrupt, or
collapsed during the ~10y backtest window is invisible to them, which
mechanically flatters a long-only equal-weight strategy. A true
point-in-time historical-constituents fix isn't available from this
project's free data sources (yfinance has no historical index-membership
API; confirmed DHFL.NS and RELCAPITAL.NS -- two real 2019-2021 NSE
large/mid-cap blowups -- return ZERO rows from yfinance, i.e. fully
invisible, no fix possible with this data source).

What IS available: yfinance still carries full historical OHLC for
several other real, famous NSE catastrophic collapses whose tickers
never stopped trading (unlike DHFL/RELCAPITAL): JETAIRWAYS.NS (grounded
2019, NCLT resolution), YESBANK.NS (~2020 near-collapse, RBI
reconstruction), RCOM.NS (Reliance Communications, insolvency), and
PCJEWELLER.NS (fraud-driven collapse) -- all -97% to -99% from peak,
verified via a direct yfinance pull before writing this script. None of
these are in either existing universe. Adding them is a real (not
synthetic) worst-case stress test: does the IBS-oversold ranking
mechanically buy into these falling knives every time they rank as most
undervalued, and if so, how much does that erode the Thirty-ninth
entry's headline numbers?
"""
import statistics

from probe_pead import UNIVERSE as LARGE_CAP_UNIVERSE
from probe_ibs_rotation_widen import SMALL_MID_CAP_UNIVERSE
from probe_ibs_rotation import (
    avg_ibs, fetch_calendar, month_end_dates, price_at_or_before,
    simulate, annualized_return_pct, _report,
)

BLOWUP_UNIVERSE = ["JETAIRWAYS.NS", "YESBANK.NS", "RCOM.NS", "PCJEWELLER.NS"]
STRESS_UNIVERSE = LARGE_CAP_UNIVERSE + SMALL_MID_CAP_UNIVERSE + BLOWUP_UNIVERSE


def build_stress_price_series(period: str, retries: int = 3):
    from data_yfinance import fetch_candles
    series = {}
    for sym in STRESS_UNIVERSE:
        candles = []
        for _ in range(retries):
            candles = fetch_candles(sym, "1d", period)
            if candles:
                break
        series[sym] = candles
    return series


def simulate_tracked(rebalance_dates, series, top_k=5, lookback=5,
                      cost_pct=0.2, dp_charge=16.0, capital=100_000.0):
    """Same mechanics as probe_ibs_rotation.simulate(), plus a per-symbol
    P&L tally and a count of how often each blowup name got picked."""
    dates_map = {s: [c["date"].date() for c in cds] for s, cds in series.items()}
    closes_map = {s: [c["close"] for c in cds] for s, cds in series.items()}
    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []
    per_symbol_pnl = {}
    pick_counts = {}

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
        for _, sym, entry_px, exit_px in picks:
            ret = (exit_px - entry_px) / entry_px
            gross = notional_each * ret
            cost = notional_each * (cost_pct / 100) * 2 + dp_charge
            net = gross - cost
            month_pnl += net
            per_symbol_pnl[sym] = per_symbol_pnl.get(sym, 0.0) + net
            pick_counts[sym] = pick_counts.get(sym, 0) + 1
        capital_track += month_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        months.append(dict(date=entry_date, pnl=month_pnl, n=len(picks)))

    total_net = sum(m["pnl"] for m in months)
    wins = sum(1 for m in months if m["pnl"] > 0)
    return dict(months=months, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_months=len(months),
                win_rate=wins / len(months) if months else 0.0,
                per_symbol_pnl=per_symbol_pnl, pick_counts=pick_counts)


def run(top_k=5, lookback=5, period="10y", capital=100_000.0):
    print(f"Stress universe: {len(STRESS_UNIVERSE)} stocks "
          f"({len(LARGE_CAP_UNIVERSE)} large-cap + {len(SMALL_MID_CAP_UNIVERSE)} small/mid-cap "
          f"+ {len(BLOWUP_UNIVERSE)} real catastrophic blowups: {BLOWUP_UNIVERSE})")
    series = build_stress_price_series(period)
    for sym in BLOWUP_UNIVERSE:
        n = len(series.get(sym, []))
        print(f"  {sym}: {n} bars fetched")

    rebalance_dates = month_end_dates(fetch_calendar(period))
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    kwargs = dict(top_k=top_k, lookback=lookback, capital=capital)

    cutoff = len(rebalance_dates) // 2
    in_s = simulate_tracked(rebalance_dates[:cutoff + 1], series, **kwargs)
    out_s = simulate_tracked(rebalance_dates[cutoff:], series, **kwargs)
    _report("In-sample  (first half)", in_s, capital, years / 2)
    _report("Out-of-sample (2nd half)", out_s, capital, years / 2)
    consistent = (in_s["final_capital"] > capital) == (out_s["final_capital"] > capital)
    print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves\n")

    n = len(rebalance_dates)
    cuts = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    quarter_pnls = []
    for i in range(4):
        chunk = rebalance_dates[cuts[i]:cuts[i + 1] + 1]
        r = simulate_tracked(chunk, series, **kwargs)
        quarter_pnls.append(r["total_net"])
        _report(f"Q{i+1}", r, capital, years / 4)
    print(f"all 4 quarters positive: {all(p > 0 for p in quarter_pnls)}\n")

    full = simulate_tracked(rebalance_dates, series, **kwargs)
    _report("Full period", full, capital, years)
    ann = annualized_return_pct(full["final_capital"], capital, years)

    print("\nBlowup-name involvement:")
    for sym in BLOWUP_UNIVERSE:
        picks = full["pick_counts"].get(sym, 0)
        pnl = full["per_symbol_pnl"].get(sym, 0.0)
        print(f"  {sym}: picked {picks}/{full['n_months']} months, "
              f"contributed {pnl:.0f} to total net P&L")

    per_sym = full["per_symbol_pnl"]
    ranked = sorted(per_sym.items(), key=lambda kv: -kv[1])
    top3_share = sum(v for _, v in ranked[:3]) / full["total_net"] * 100 if full["total_net"] else float("nan")
    print(f"\ntop-3 contributors' share of total net P&L: {top3_share:.1f}%")
    print("top 5 contributors:", [(s, round(v, 0)) for s, v in ranked[:5]])
    print("worst 5 contributors:", [(s, round(v, 0)) for s, v in ranked[-5:]])

    return dict(full=full, quarter_pnls=quarter_pnls, ann=ann, consistent=consistent)


if __name__ == "__main__":
    print("=== Default config (top_k=5, lookback=5) on stress universe ===")
    base = run(top_k=5, lookback=5)

    print("\n=== Sensitivity: does result hold at other configs on stress universe? ===")
    for tk, lb in [(3, 10), (7, 1), (10, 5)]:
        print(f"\n--- top_k={tk}, lookback={lb} ---")
        run(top_k=tk, lookback=lb)
