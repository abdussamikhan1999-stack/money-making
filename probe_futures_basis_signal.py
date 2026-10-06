"""
Hundred-and-eleventh entry: Nifty futures basis (cost-of-carry) as a market-timing signal —
genuinely new data dimension never tested in this project: the GAP between the near-month NIFTY
index future and the spot index, not price, volume, OI level, put/call ratio, delivery %, a
disclosed trade, or the calendar. Reuses `probe_iron_condor_real_data.py`'s `fetch_fo_bhavcopy`/
`weekly_mondays` fetch machinery (per that entry's own stated recommendation) and
`probe_pcr_signal.py`'s entire weekly-cadence trade/significance harness (`rolling_percentile`,
`quarter_split`, `walk_forward`, `report`, its cost model and CAPITAL) unchanged — only the signal
itself (basis instead of put/call OI) and its fetch function are new.

WHAT IT IS: basis_pct(t) = FUTIDX NIFTY near-month close(t) / ^NSEI spot close(t) - 1. A positive
basis (contango) is normal (futures price in the risk-free rate net of dividends); this signal is
about the basis's own RELATIVE level over time (rolling percentile, same construction as PCR's),
not its absolute sign — a widening/narrowing premium reflects how aggressively leveraged money is
positioned long via futures relative to its own recent history.

DIRECTION IS NOT FIXED IN ADVANCE: retail/institutional commentary on Nifty futures basis argues
both ways (momentum: a widening premium = aggressive bullish positioning = continuation; contrarian:
a stretched premium = crowded/over-leveraged long = reversion, the same logic PCR's own entry
applied to put/call extremes) and no single canonical published direction exists the way it does
for, say, overnight drift or the pre-holiday effect. Both directions are tested as named hypotheses
and reported regardless of sign, the same convention Entries 92/94/95/96 use for day-of-week/lunar/
round-number/Mercury-retrograde where the literature itself is split or purely descriptive.

PRE-REGISTERED GRID (before any real number was scored): entry_threshold in {0.85, 0.90} x window
in {26, 52} weeks x direction in {"high", "low"} = 8 configs. "high" tests the momentum/continuation
hypothesis (go long NIFTYBEES while basis sits in the top of its own rolling window); "low" tests
the contrarian hypothesis (go long while basis sits in the bottom). exit_threshold = 1 - entry's own
distance from the extreme (symmetric unwind, matching PCR's own entry/exit shape), max_hold_weeks=8.
Traded via NIFTYBEES.NS (as PCR's entry did) rather than Nifty futures/options, so this is NOT
blocked by the fixed-lot capital-tier wall that closed every prior Nifty-derivative idea in this
project — spot for the SIGNAL itself is ^NSEI (not NIFTYBEES, whose Yahoo Open/High/Low were found
corrupt in the Seventy-ninth entry; only NIFTYBEES's Close is used, for the executed trade, same
reasoning that entry already applied).

SIGNIFICANCE: `probe_pcr_signal.shift_control`'s own circular-rotation null (a random offset >= 1
year, keeps the signal's own autocorrelation and trade cadence, destroys any real link to NIFTYBEES'
price path) — reused verbatim via the same function, just called with this file's own `simulate`.

DECISION RULE: a config "advances" only if p(random >= actual) < 0.05 (uncorrected first), both
walk-forward halves are net-positive, and the net annualized return clears zero after this project's
standard equity cost model (already included in `simulate`'s per-trade P&L). All 8 configs are
registered in `multiple_comparisons.py` regardless of outcome, per this project's honesty convention.
"""

import argparse
import bisect
import datetime
import os
import time

import yfinance as yf

from probe_iron_condor_real_data import fetch_fo_bhavcopy, weekly_mondays
from probe_pcr_signal import (
    rolling_percentile,
    quarter_split,
    walk_forward,
    report,
    COST_PCT_PER_LEG,
    DP_CHARGE,
    CAPITAL,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE = os.path.join(_HERE, "futures_basis_weekly_series.csv")


def nifty_near_month_futures_close(text, fmt):
    """Return the NIFTY index future's near-month close from one day's bhavcopy, or None."""
    lines = text.splitlines()
    fut_closes = []
    if fmt == "old":
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < 10:
                continue
            if parts[0] == "FUTIDX" and parts[1] == "NIFTY":
                try:
                    expiry = datetime.datetime.strptime(parts[2], "%d-%b-%Y").date()
                    fut_closes.append((expiry, float(parts[8])))
                except ValueError:
                    continue
    else:
        header = lines[0].split(",")
        idx = {name: i for i, name in enumerate(header)}
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < len(header) - 4:
                continue
            if parts[idx["TckrSymb"]] != "NIFTY" or parts[idx["FinInstrmTp"]] != "IDF":
                continue
            try:
                expiry = datetime.datetime.strptime(parts[idx["XpryDt"]], "%Y-%m-%d").date()
                fut_closes.append((expiry, float(parts[idx["ClsPric"]])))
            except (ValueError, IndexError):
                continue
    if not fut_closes:
        return None
    return min(fut_closes, key=lambda x: x[0])[1]


def fetch_spot_closes(ticker, start, end):
    df = yf.Ticker(ticker).history(
        start=start - datetime.timedelta(days=10), end=end + datetime.timedelta(days=10)
    )
    out = sorted((ts.date(), float(row["Close"])) for ts, row in df.iterrows())
    if not out:
        raise RuntimeError(f"{ticker} price fetch returned no rows (yfinance failure?)")
    return out


def price_on_or_before(closes, d):
    i = bisect.bisect_right(closes, (d, float("inf")))
    return closes[i - 1][1] if i > 0 else None


def price_on_or_after(closes, d):
    i = bisect.bisect_left(closes, (d,))
    return closes[i][1] if i < len(closes) else None


def fetch_weekly_basis(start, end, spot_closes, verbose=True):
    series = []
    errors = []
    for i, monday in enumerate(weekly_mondays(start, end)):
        result = None
        for offset in range(4):
            d = monday + datetime.timedelta(days=offset)
            if d > end:
                break
            try:
                text, fmt = fetch_fo_bhavcopy(d)
                fut_close = nifty_near_month_futures_close(text, fmt)
                if fut_close is None:
                    continue
                spot = price_on_or_before(spot_closes, d)
                if spot is None or spot <= 0:
                    continue
                result = (d, fut_close / spot - 1.0)
                break
            except Exception:
                continue
            time.sleep(0.2)
        if result:
            series.append(result)
        else:
            errors.append(monday)
        if verbose and (i + 1) % 50 == 0:
            print(f"  ...{i + 1} weeks, {len(series)} OK, {len(errors)} errors")
        time.sleep(0.2)
    return series, errors


def simulate(series, closes, entry_threshold, exit_threshold, window, max_hold_weeks,
             direction, lag_days=1):
    """direction='high': go long while the basis sits in the TOP of its own rolling window
    (momentum/continuation hypothesis). direction='low': go long while it sits in the BOTTOM
    (contrarian hypothesis) — tested by mirroring the percentile (1 - pct) so the same
    entry/exit-threshold shape works for both without a second code path."""
    dates = [d for d, _ in series]
    vals = [v for _, v in series]
    raw_pct = rolling_percentile(vals, window)
    if direction == "high":
        percentiles = raw_pct
    elif direction == "low":
        percentiles = [None if p is None else 1.0 - p for p in raw_pct]
    else:
        raise ValueError(direction)

    trades = []
    in_position = False
    entry_price = entry_date = weeks_held = None

    for i, d in enumerate(dates):
        pct = percentiles[i]
        # Bhavcopy OI/futures data is only published after the close, so fill at the first
        # close STRICTLY after the signal date (lag_days=1), not the same bar.
        price = price_on_or_after(closes, d + datetime.timedelta(days=lag_days))
        if price is None:
            continue
        if not in_position:
            if pct is not None and pct >= entry_threshold:
                in_position = True
                entry_price = price
                entry_date = d
                weeks_held = 0
        else:
            weeks_held += 1
            exit_now = (pct is not None and pct <= exit_threshold) or weeks_held >= max_hold_weeks
            if exit_now:
                gross_ret = price / entry_price - 1.0
                net_ret = gross_ret - COST_PCT_PER_LEG / 100 * 2 - DP_CHARGE / CAPITAL
                trades.append(
                    {"entry_date": entry_date, "exit_date": d, "pnl": net_ret * CAPITAL}
                )
                in_position = False

    return trades


def shift_control(series, closes, entry_threshold, exit_threshold, window, max_hold_weeks,
                   direction, actual_total, n_seeds, seed=0):
    import random

    rng = random.Random(seed)
    dates = [d for d, _ in series]
    vals = [v for _, v in series]
    n = len(vals)
    totals = []
    for _ in range(n_seeds):
        k = rng.randrange(52, n - 52)
        shifted = list(zip(dates, vals[k:] + vals[:k]))
        tr = simulate(shifted, closes, entry_threshold, exit_threshold, window,
                      max_hold_weeks, direction)
        totals.append(sum(t["pnl"] for t in tr))
    p = (sum(1 for t in totals if t >= actual_total) + 1) / (len(totals) + 1)
    mean = sum(totals) / len(totals)
    return p, mean


def load_or_fetch_series(start, end, cache_path, spot_closes):
    series = None
    if os.path.exists(cache_path):
        cached = []
        with open(cache_path) as f:
            for line in f:
                d, v = line.strip().split(",")
                cached.append((datetime.date.fromisoformat(d), float(v)))
        if cached and cached[0][0] <= start + datetime.timedelta(days=7) and cached[-1][0] >= end - datetime.timedelta(days=14):
            print(f"Loading cached basis series from {cache_path}")
            series = [(d, v) for d, v in cached if start <= d <= end]
        else:
            print(f"Cache {cache_path} covers {cached[0][0] if cached else None}.."
                  f"{cached[-1][0] if cached else None}, not {start}..{end}; refetching")
    if series is None:
        print(f"Fetching weekly NIFTY futures basis {start} to {end}...")
        series, errors = fetch_weekly_basis(start, end, spot_closes)
        print(f"Fetched {len(series)} weeks OK, {len(errors)} errors")
        if errors:
            print(f"WARNING: {len(errors)} weeks failed; NOT writing the cache")
        else:
            with open(cache_path, "w") as f:
                for d, v in series:
                    f.write(f"{d.isoformat()},{v}\n")
    return series


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--control-seeds", type=int, default=0)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--entry-threshold", type=float, default=0.90)
    ap.add_argument("--window", type=int, default=26)
    ap.add_argument("--direction", choices=["high", "low"], default="high")
    ap.add_argument("--max-hold-weeks", type=int, default=8)
    args = ap.parse_args()

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)

    print("Fetching ^NSEI spot closes...")
    spot_closes = fetch_spot_closes("^NSEI", start, end)
    series = load_or_fetch_series(start, end, args.cache, spot_closes)

    print("Fetching NIFTYBEES.NS closes (execution instrument)...")
    closes = fetch_spot_closes("NIFTYBEES.NS", start, end)

    if not args.grid:
        exit_threshold = 1.0 - args.entry_threshold
        trades = simulate(series, closes, args.entry_threshold, exit_threshold, args.window,
                           args.max_hold_weeks, args.direction)
        total, *_ = report(trades, start, end,
                            label=f"direction={args.direction} entry={args.entry_threshold} window={args.window}")
        if args.control_seeds:
            p, mean = shift_control(series, closes, args.entry_threshold, exit_threshold,
                                     args.window, args.max_hold_weeks, args.direction, total,
                                     args.control_seeds)
            print(f"  circular-shift control ({args.control_seeds} seeds): random mean Rs {mean:,.0f}, "
                  f"p(random >= actual) = {p:.4f}")
        return

    print("\n=== Pre-registered grid: entry_threshold x window x direction ===")
    for direction in ("high", "low"):
        for entry_th in (0.85, 0.90):
            for window in (26, 52):
                exit_th = 1.0 - entry_th
                trades = simulate(series, closes, entry_th, exit_th, window,
                                   args.max_hold_weeks, direction)
                total = sum(t["pnl"] for t in trades)
                wf_first, wf_second = walk_forward(trades)
                consistent = wf_first > 0 and wf_second > 0
                years = (end - start).days / 365.25
                ann = (total / CAPITAL) / years * 100 if years > 0 else 0.0
                p = mean = None
                if args.control_seeds:
                    p, mean = shift_control(series, closes, entry_th, exit_th, window,
                                             args.max_hold_weeks, direction, total,
                                             args.control_seeds)
                print(f"dir={direction:<4} entry={entry_th:.2f} window={window:>3}: "
                      f"{len(trades):>3} trades, Rs {total:>10,.0f} ({ann:>6.2f}%/yr), "
                      f"wf first/second Rs {wf_first:,.0f}/{wf_second:,.0f} "
                      f"[{'CONSISTENT' if consistent else 'inconsistent'}]"
                      + (f", p={p:.4f} (random mean Rs {mean:,.0f})" if p is not None else ""))


if __name__ == "__main__":
    main()
