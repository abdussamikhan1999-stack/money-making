"""
Probe: the pre-holiday effect (Lakonishok & Smidt (1988), "Are Seasonal
Anomalies Real? A Ninety-Year Perspective"; Ariel (1990), "High Stock
Returns before Holidays") — the trading day immediately before an
exchange holiday has historically shown returns many times the average
daily return, one of the older and, in the original US sample, larger
documented calendar anomalies. Genuinely different in construction from
every calendar effect already tested here: day-of-week (Ninety-second
entry) and turn-of-month (Sixth entry) key off a FIXED calendar
position; this keys off an IRREGULAR event (an exchange closure) that
neither weekday nor day-of-month captures on its own (a holiday can fall
on any weekday, and this project's day-of-week entry does not condition
on what follows/precedes a given weekday).

NO EXTERNAL HOLIDAY CALENDAR (ladder rung 2/3 — the data already
contains what's needed, no new dependency or hardcoded date list):
holidays are inferred directly from GAPS in the trading-day sequence
itself. For trading day t, the "normal" gap to the next trading day is 1
calendar day (Mon-Thu) or 3 (Friday -> Monday); if the ACTUAL gap is
larger, an exchange holiday fell in between. That makes t "pre-holiday"
and the day after the gap "post-holiday" — this generalizes correctly
regardless of which weekday the holiday itself falls on (including
holidays adjacent to a weekend, which just produce a wider gap from the
preceding Thursday/Friday) and needs nothing beyond the OHLC data this
project already fetches.

Pre-registered before any return was computed, same convention as the
Ninety-second entry: mean return on pre-holiday and post-holiday days vs
baseline, on NIFTY (20y) and the S&P 500 (~98y), the same random-
same-size-subset null used throughout this project's calendar-effect
entries. The literature's predicted direction (pre-holiday > baseline)
is named, not assumed; reported regardless of sign.
"""
import argparse
from datetime import datetime

import numpy as np

from data_yfinance import fetch_candles


def classify_holiday_days(dates):
    """(pre, post) boolean arrays, same length as `dates`. pre[i] = the
    gap from date i to date i+1 exceeds the normal weekday gap; post[i] =
    date i follows such a gap."""
    n = len(dates)
    pre = np.zeros(n, dtype=bool)
    post = np.zeros(n, dtype=bool)
    for i in range(n - 1):
        gap = (dates[i + 1] - dates[i]).days
        normal = 3 if dates[i].weekday() == 4 else 1  # Friday -> Monday is normal
        if gap > normal:
            pre[i] = True
            post[i + 1] = True
    return pre, post


def returns_by_holiday(symbol: str, period: str):
    candles = fetch_candles(symbol, "1d", period)
    closes = np.array([c["close"] for c in candles])
    dates = [d.date() if isinstance(d, datetime) else d for d in (c["date"] for c in candles)]
    rets = closes[1:] / closes[:-1] - 1
    pre, post = classify_holiday_days(dates)
    # rets[k] is dates[k+1]'s own return (close/prev_close - 1); mask with
    # pre/post shifted the same way to line up with dates[1:].
    day_ret = rets
    pre_mask = pre[1:]
    post_mask = post[1:]
    baseline_mask = ~pre_mask & ~post_mask
    return {"pre": day_ret[pre_mask], "post": day_ret[post_mask], "other": day_ret[baseline_mask]}, day_ret


def subset_control(rets: np.ndarray, n: int, draws: int, rng: np.random.Generator):
    out = np.empty(draws)
    idx = np.arange(len(rets))
    for i in range(draws):
        sample = rng.choice(idx, size=n, replace=False)
        out[i] = rets[sample].mean()
    return out


def report(symbol: str, period: str, draws: int, seed: int = 0):
    by_label, rets = returns_by_holiday(symbol, period)
    rng = np.random.default_rng(seed)
    print(f"\n{symbol}: {len(rets)} trading days, {period}; overall mean daily return {rets.mean():.4%}")
    rows = []
    for label in ("pre", "post", "other"):
        r = by_label[label]
        n = len(r)
        actual = r.mean()
        null = subset_control(rets, n, draws, rng)
        p = ((np.abs(null) >= abs(actual)).sum() + 1) / (draws + 1)
        half = n // 2
        h1, h2 = r[:half].mean(), r[half:].mean()
        print(f"  {label:5s}: n={n} mean={actual:.4%} pos_frac={(r>0).mean():.1%} "
              f"halves=({h1:.4%},{h2:.4%}) null_mean={null.mean():.4%} p={p:.4f}")
        rows.append((symbol, label, actual, n, p))
    print(f"  pre - other = {by_label['pre'].mean() - by_label['other'].mean():.4%} "
          f"(literature predicts pre > other)")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period-nifty", default="20y")
    parser.add_argument("--period-spx", default="max")
    parser.add_argument("--draws", type=int, default=5000)
    args = parser.parse_args()

    all_rows = []
    all_rows += report("^NSEI", args.period_nifty, args.draws)
    all_rows += report("^GSPC", args.period_spx, args.draws)

    print("\n=== summary (uncorrected p < 0.05) ===")
    for sym, label, mean, n, p in all_rows:
        if p < 0.05:
            print(f"  {sym} {label}: n={n} mean={mean:.4%} p={p:.4f}")
