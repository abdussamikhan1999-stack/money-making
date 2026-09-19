"""
Nifty options Put-Call Ratio (PCR) — a contrarian POSITIONING/SENTIMENT
signal, genuinely different in kind from every prior mechanism in this
project: every strategy tried so far (CLAUDE.md entries 1-55) derives its
signal from price, volume, or a fundamental factor. This one derives it
from OPEN INTEREST in the options market — how much capital is actually
positioned long puts vs. long calls — using real NSE F&O bhavcopy data
(reusing `probe_iron_condor_real_data.py`'s `fetch_fo_bhavcopy`/
`weekly_mondays` fetch machinery unchanged, per that entry's own
recommendation to reuse it for any future options-data-dependent probe).

Standard interpretation (widely used by NSE-focused derivatives desks):
PCR = total put OI / total call OI, summed across all strikes and expiries
for NIFTY index options on a given day. A very HIGH PCR means the market
is heavily positioned in puts relative to calls — read as excessive fear/
hedging, a contrarian BULLISH signal (crowd positioning is stretched one
way, due to mean-revert). A very LOW PCR is the mirror-image contrarian
BEARISH signal. Only the long/high-PCR side is tested here: shorting has
the same NSE cash-equity SLB/short-availability caveat already flagged for
the per-stock and NIFTYBEES-ETF hedges (Forty-fourth/Forty-fifth/
Forty-sixth CLAUDE.md entries) — no SLB or short cost modeled anywhere in
this project.

Traded via NIFTYBEES.NS (the same ETF proxy already validated for the
Forty-sixth entry's beta hedge) instead of Nifty futures/options, so this
strategy is NOT blocked by the fixed-lot capital-tier wall that closed out
every prior Nifty-derivative idea in this project (options selling, the
futures beta hedge) — any whole-share quantity is tradable at this
project's target capital.

PCR's absolute level drifts over multi-year periods (structural OI growth,
changing market composition), so "extreme" is defined as a rolling
percentile rank within a trailing window of `window` weekly observations,
not a fixed absolute threshold — the same relative-ranking approach this
project's `regime.py` already uses for its own volatility bucket.

Weekly cadence (one bhavcopy fetch per week, Monday or nearest trading
day, matching the Fourteenth entry's own iron-condor cycle) — PCR is a
slow-moving positioning measure, not a daily timing trigger, and weekly
sampling keeps the fetch cost (~500 requests over a 10y backtest) in the
same ballpark as that entry's own 140-cycle run.

Position sizing is intentionally simple, matching several of this
project's other single-instrument probes (e.g. `probe_overnight.py`,
`probe_gap_fill.py`): a FIXED notional per trade (no compounding), % return
per trade net of the project's standard equity cost model (0.2% STT+stamp
each way + a flat Rs16 DP charge on the sell leg).
"""

import argparse
import bisect
import datetime
import random
import time

import yfinance as yf

from probe_iron_condor_real_data import fetch_fo_bhavcopy, weekly_mondays

COST_PCT_PER_LEG = 0.2
DP_CHARGE = 16.0
CAPITAL = 100_000.0


def nifty_total_oi(text, fmt):
    """Sum PUT and CALL open interest across all NIFTY index-option rows
    (all strikes, all expiries) in one day's bhavcopy."""
    lines = text.splitlines()
    put_oi = 0
    call_oi = 0
    if fmt == "old":
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < 13:
                continue
            if parts[0] == "OPTIDX" and parts[1] == "NIFTY":
                try:
                    oi = int(parts[12])
                except ValueError:
                    continue
                if parts[4] == "PE":
                    put_oi += oi
                elif parts[4] == "CE":
                    call_oi += oi
    else:
        header = lines[0].split(",")
        idx = {name: i for i, name in enumerate(header)}
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < len(header) - 4:
                continue
            if parts[idx["TckrSymb"]] != "NIFTY" or parts[idx["FinInstrmTp"]] != "IDO":
                continue
            try:
                oi = int(parts[idx["OpnIntrst"]])
            except (ValueError, IndexError):
                continue
            if parts[idx["OptnTp"]] == "PE":
                put_oi += oi
            elif parts[idx["OptnTp"]] == "CE":
                call_oi += oi
    return put_oi, call_oi


def fetch_weekly_pcr(start, end, verbose=True):
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
                put_oi, call_oi = nifty_total_oi(text, fmt)
                if call_oi > 0:
                    result = (d, put_oi / call_oi)
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


def rolling_percentile(values, window):
    """Percentile rank of values[i] within the trailing `window` observations
    (inclusive of today) — None until the window first fills, no lookahead."""
    out = [None] * len(values)
    for i in range(len(values)):
        lo = i - window + 1
        if lo < 0:
            continue
        hist = values[lo : i + 1]
        out[i] = sum(1 for v in hist if v <= values[i]) / len(hist)
    return out


def fetch_etf_closes(start, end):
    df = yf.Ticker("NIFTYBEES.NS").history(
        start=start - datetime.timedelta(days=10), end=end + datetime.timedelta(days=10)
    )
    return sorted((ts.date(), float(row["Close"])) for ts, row in df.iterrows())


def price_on_or_after(closes, d):
    i = bisect.bisect_left(closes, (d,))
    return closes[i][1] if i < len(closes) else None


def simulate(series, closes, entry_threshold, exit_threshold, window, max_hold_weeks, lag_days=1):
    dates = [d for d, _ in series]
    pcrs = [v for _, v in series]
    percentiles = rolling_percentile(pcrs, window)

    trades = []
    in_position = False
    entry_price = entry_date = weeks_held = None

    for i, d in enumerate(dates):
        pct = percentiles[i]
        # Bhavcopy OI is only published after the close, so fill at the first
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


def shift_control(series, closes, args, actual_total, n_seeds, seed=0):
    """Circularly rotate the PCR values against the price dates by a random
    offset (>= 1 year away from 0) and rerun the identical strategy. Keeps the
    signal's own autocorrelation and trade cadence but destroys any real
    timing relationship to NIFTYBEES, so the resulting P&L distribution is
    'what this rule earns on a long-only ETF by luck of bull-market drift'."""
    rng = random.Random(seed)
    dates = [d for d, _ in series]
    vals = [v for _, v in series]
    n = len(vals)
    totals = []
    for _ in range(n_seeds):
        k = rng.randrange(52, n - 52)
        shifted = list(zip(dates, vals[k:] + vals[:k]))
        tr = simulate(shifted, closes, args.entry_threshold, args.exit_threshold,
                      args.window, args.max_hold_weeks)
        totals.append(sum(t["pnl"] for t in tr))
    p = sum(1 for t in totals if t >= actual_total) / len(totals)
    mean = sum(totals) / len(totals)
    return p, mean


def quarter_split(trades, start, end):
    if not trades:
        return {}
    total_days = (end - start).days
    chunk_days = total_days / 4
    buckets = {1: [], 2: [], 3: [], 4: []}
    for t in trades:
        offset = (t["entry_date"] - start).days
        q = min(4, int(offset // chunk_days) + 1)
        buckets[q].append(t["pnl"])
    return {q: sum(pnls) for q, pnls in buckets.items()}


def walk_forward(trades):
    n = len(trades)
    half = n // 2
    return sum(t["pnl"] for t in trades[:half]), sum(t["pnl"] for t in trades[half:])


def report(trades, start, end, label=""):
    total = sum(t["pnl"] for t in trades)
    years = (end - start).days / 365.25
    ann = (total / CAPITAL) / years * 100 if years > 0 else 0.0
    wins = sum(1 for t in trades if t["pnl"] > 0)
    wf_first, wf_second = walk_forward(trades)
    qs = quarter_split(trades, start, end)
    print(f"\n{label}: {len(trades)} trades, net P&L Rs {total:,.0f} ({ann:.2f}%/yr), "
          f"win rate {100*wins/len(trades) if trades else 0:.0f}%")
    print(f"  walk-forward: first Rs {wf_first:,.0f} / second Rs {wf_second:,.0f} "
          f"({'CONSISTENT' if wf_first > 0 and wf_second > 0 else 'INCONSISTENT'})")
    print(f"  quarters: " + ", ".join(f"Q{q} Rs{v:,.0f}" for q, v in sorted(qs.items())))
    return total, ann, wf_first, wf_second, qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--entry-threshold", type=float, default=0.90)
    ap.add_argument("--exit-threshold", type=float, default=0.50)
    ap.add_argument("--window", type=int, default=26)
    ap.add_argument("--max-hold-weeks", type=int, default=8)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--control-seeds", type=int, default=0)
    ap.add_argument("--cache", default="pcr_weekly_series.csv")
    args = ap.parse_args()

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)

    import os

    if os.path.exists(args.cache):
        print(f"Loading cached PCR series from {args.cache}")
        series = []
        with open(args.cache) as f:
            for line in f:
                d, v = line.strip().split(",")
                series.append((datetime.date.fromisoformat(d), float(v)))
    else:
        print(f"Fetching weekly NIFTY PCR {start} to {end}...")
        series, errors = fetch_weekly_pcr(start, end)
        print(f"Fetched {len(series)} weeks OK, {len(errors)} errors")
        with open(args.cache, "w") as f:
            for d, v in series:
                f.write(f"{d.isoformat()},{v}\n")

    print(f"Fetching NIFTYBEES.NS closes...")
    closes = fetch_etf_closes(start, end)

    if not args.sweep:
        trades = simulate(series, closes, args.entry_threshold, args.exit_threshold,
                           args.window, args.max_hold_weeks)
        total, *_ = report(trades, start, end, label=f"entry={args.entry_threshold} window={args.window}")
        if trades:
            held = sum((t["exit_date"] - t["entry_date"]).days for t in trades)
            print(f"  time in market: {100*held/(end-start).days:.0f}%")
        if args.control_seeds:
            p, mean = shift_control(series, closes, args, total, args.control_seeds)
            print(f"  circular-shift control ({args.control_seeds} seeds): random mean Rs {mean:,.0f}, "
                  f"p(random >= actual) = {p:.3f}")
        return

    print("\n=== Perturbation sweep ===")
    for entry_th in (0.80, 0.85, 0.90, 0.95):
        for window in (13, 26, 52):
            trades = simulate(series, closes, entry_th, args.exit_threshold, window,
                               args.max_hold_weeks)
            wf_first, wf_second = walk_forward(trades)
            consistent = wf_first > 0 and wf_second > 0
            total = sum(t["pnl"] for t in trades)
            print(f"entry={entry_th:.2f} window={window:>3}: {len(trades):>3} trades, "
                  f"Rs {total:>10,.0f}, wf first/second Rs {wf_first:,.0f}/{wf_second:,.0f} "
                  f"[{'CONSISTENT' if consistent else 'inconsistent'}]")


if __name__ == "__main__":
    main()
