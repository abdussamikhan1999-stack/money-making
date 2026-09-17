"""Probe: monthly cross-sectional IBS rotation — a LOW-FREQUENCY portfolio
version of Internal Bar Strength (Thirteenth entry, real but thin: one
gold survivor, 12.5% hit rate, chance-level).

Substituted in for the originally-planned 12-1 skip-month cross-sectional
momentum (Jegadeesh & Titman) after confirming the Fourth entry already
tested this project's own 6-1 monthly-rebalanced cross-sectional momentum
variant and it decayed to a Q4 loss — a 12-month lookback is the same
basket-wide-directional shape this project's Q4 synthesis already
distrusts, not a genuinely different mechanism. Sector-index rotation was
also considered and ruled out on a data-feasibility check: only
^CNXIT/^CNXPHARMA/^NSEBANK have >1 day of history via yfinance, too few
sectors to rotate across.

What IS genuinely different here: same signal (IBS = where today's close
sits in today's high-low range) as the Thirteenth entry, but turned into a
CROSS-SECTIONAL MONTHLY RANK across a 40-stock universe (long the most
oversold names each month-end, equal-weighted, held one month) instead of
a daily per-instrument trigger. Per this project's Q4 regime synthesis,
single-instrument mean-reversion signals have held up in the most recent
quarter where basket-wide momentum hasn't — this tests whether that same
mean-reversion character survives being aggregated cross-sectionally and
slowed to monthly rebalance (~12 decisions/year, reusing momentum
rotation's zero-delivery-brokerage cost model).
"""
import argparse
import bisect

from probe_pead import UNIVERSE
from indicators import internal_bar_strength


def build_price_series(period: str, retries: int = 3):
    """yfinance intermittently drops a ticker's response under rapid repeat
    calls ("possibly delisted" on names that plainly aren't, e.g.
    SUNPHARMA/TATASTEEL) — retried here because a silently-missing symbol
    changes which stocks are eligible for a given month's top-K picks,
    which changes results run-to-run (caught by a walk-forward vs.
    quarter-split invocation disagreeing on the SAME default parameters)."""
    from data_yfinance import fetch_candles
    series = {}
    for sym in UNIVERSE:
        candles = []
        for _ in range(retries):
            candles = fetch_candles(sym, "1d", period)
            if candles:
                break
        series[sym] = candles
    return series


def fetch_calendar(period: str) -> list[dict]:
    """^NSEI as the trading-day calendar (not `max(series.values(), key=len)`
    over the 40-stock universe) — several universe stocks tie on trading-day
    count, so picking "whichever is longest" was tie-breaking on dict
    iteration order and silently shifting month-end dates run-to-run."""
    from data_yfinance import fetch_candles
    return fetch_candles("^NSEI", "1d", period)


def latest_settled_date(calendar_candles: list[dict], now=None):
    """The last date in calendar_candles whose daily bar is actually
    FINAL, not a live/still-forming intraday candle. yfinance's "today"
    1d bar keeps changing (high/low/close all move) while NSE cash
    market is open (09:15-15:30 IST) -- ranking against it during market
    hours makes two live-tracker runs minutes apart compute genuinely
    different IBS scores for the same symbols, not just retry noise (see
    entry 49, which mistook this for the Thirty-eighth entry's
    already-documented missing-symbol retry variance before tracing it
    to this). Used only by the live paper-trackers; backtests always run
    on historical (already-settled) periods, so this never changes their
    numbers."""
    import datetime
    if now is None:
        now = datetime.datetime.now()
    dates = [c["date"].date() for c in calendar_candles]
    if dates and dates[-1] == now.date() and now.time() < datetime.time(15, 45):
        return dates[-2] if len(dates) > 1 else None
    return dates[-1] if dates else None


def month_end_dates(calendar_candles: list[dict]) -> list:
    dates = [c["date"].date() for c in calendar_candles]
    ends = []
    for i in range(len(dates) - 1):
        if dates[i].month != dates[i + 1].month:
            ends.append(dates[i])
    if dates:
        ends.append(dates[-1])
    return ends


def avg_ibs(candles: list[dict], dates: list, as_of, lookback: int) -> float | None:
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < lookback - 1:
        return None
    window = candles[idx - lookback + 1: idx + 1]
    scores = [s for s in (internal_bar_strength(b) for b in window) if s is not None]
    return sum(scores) / len(scores) if scores else None


def price_at_or_before(dates: list, closes: list, as_of):
    idx = bisect.bisect_right(dates, as_of) - 1
    return closes[idx] if idx >= 0 else None


def dates_closes_maps(series: dict) -> tuple[dict, dict]:
    dates_map = {s: [c["date"].date() for c in cds] for s, cds in series.items()}
    closes_map = {s: [c["close"] for c in cds] for s, cds in series.items()}
    return dates_map, closes_map


def rank_by_ibs(series: dict, dates_map: dict, closes_map: dict, as_of, lookback: int,
                 top_k: int) -> list[tuple[float, str, float]]:
    """Most-oversold-first ranking (ascending avg IBS) as of a given date,
    each entry (score, symbol, price_at_or_before(as_of)). Shared by
    simulate() and paper_track_ibs_rotation.py so the live picker can never
    silently drift from what was actually backtested."""
    scored = []
    for sym, candles in series.items():
        score = avg_ibs(candles, dates_map[sym], as_of, lookback)
        px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if score is None or px is None:
            continue
        scored.append((score, sym, px))
    scored.sort(key=lambda t: t[0])
    return scored[:top_k]


def simulate(rebalance_dates: list, series: dict, top_k: int = 5, lookback: int = 5,
             cost_pct: float = 0.2, dp_charge: float = 16.0, capital: float = 100_000.0):
    dates_map, closes_map = dates_closes_maps(series)

    capital_track = capital
    peak = capital
    max_dd = 0.0
    months = []

    for i in range(len(rebalance_dates) - 1):
        entry_date, exit_date = rebalance_dates[i], rebalance_dates[i + 1]
        picks = rank_by_ibs(series, dates_map, closes_map, entry_date, lookback, top_k)
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


def annualized_return_pct(final_capital: float, capital: float, years: float) -> float:
    if years <= 0:
        return 0.0
    total_return = final_capital / capital - 1
    if total_return <= -1:
        return -100.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def _report(label, result, capital, years):
    ann = annualized_return_pct(result["final_capital"], capital, years)
    print(f"{label}: months={result['n_months']} win_rate={result['win_rate']*100:.0f}% "
          f"net={result['total_net']:.0f} final_cap={result['final_capital']:.0f} "
          f"max_dd={result['max_dd']:.1f}% annualized={ann:.2f}%/yr")


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
    parser.add_argument("--lookback", type=int, default=5)
    parser.add_argument("--cost-pct", type=float, default=0.2)
    parser.add_argument("--dp-charge", type=float, default=16.0)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    series = build_price_series(args.period)
    rebalance_dates = month_end_dates(fetch_calendar(args.period))
    kwargs = dict(top_k=args.top_k, lookback=args.lookback, capital=args.capital,
                  cost_pct=args.cost_pct, dp_charge=args.dp_charge)
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
    else:
        result = simulate(rebalance_dates, series, **kwargs)
        _report("Full period", result, args.capital, years)
