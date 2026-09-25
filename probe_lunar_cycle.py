"""
Probe: the lunar-phase ("astrology") effect — Yuan, Zheng & Zhu (2006),
"Are investors moonstruck? Lunar phases and stock returns" (Journal of
Empirical Finance), found stock returns around new moon are reliably
higher than returns around full moon across 48 countries, and floated
investor-mood/sleep-disruption as the behavioral channel. A real,
published, peer-reviewed calendar anomaly this project has never
touched, genuinely different from every OTHER calendar effect tested so
far (day-of-week, turn-of-month, Halloween, January): those are all
keyed to the CALENDAR (weekday/month), this is keyed to an independent
astronomical cycle (the 29.53-day synodic month) that drifts relative to
weekdays and months, so it can't be a repackaging of an already-tested
effect.

MOON PHASE: no ephemeris library added (ladder rung 3 — stdlib/plain
math covers it) — phase is computed from days-since-a-known-reference-
new-moon (2000-01-06, a standard reference date) modulo the synodic
month length (29.530588853 days), giving phase in [0, 1) where 0/1.0 =
new moon, 0.5 = full moon. Calendar-day granularity (not intraday), which
introduces at most a few hours of error against the true instant of
each phase — immaterial at daily-return resolution and the same
precision used by the original paper's own day-level methodology.

Pre-registered before any return was computed, same convention as the
Ninety-second entry's day-of-week test: classify each trading day as
"new" (within `--window-days` of the nearest new moon), "full" (within
`--window-days` of the nearest full moon), or "other"; test both
new-vs-baseline and full-vs-baseline on two independent long-history
markets (NIFTY, S&P 500), reporting regardless of sign — the literature's
own predicted direction (new > full) is one of four cells, not assumed.
NULL: the same `draws` random-same-size-subset-of-all-trading-days
control already used by `probe_day_of_week.py`, applied here to a
different day-classification criterion.
"""
import argparse
from datetime import date, datetime

import numpy as np

from data_yfinance import fetch_candles

SYNODIC_DAYS = 29.530588853
REFERENCE_NEW_MOON = date(2000, 1, 6)


def moon_phase(d) -> float:
    """Fraction of the synodic month elapsed since the reference new moon;
    0/1.0 = new moon, 0.5 = full moon. Accepts a date or datetime."""
    d = d.date() if isinstance(d, datetime) else d
    days_since = (d - REFERENCE_NEW_MOON).days
    return (days_since % SYNODIC_DAYS) / SYNODIC_DAYS


def classify(d, window_days: float) -> str:
    phase = moon_phase(d)
    window_frac = window_days / SYNODIC_DAYS
    dist_to_new = min(phase, 1 - phase)  # distance to phase 0 (wrapping)
    dist_to_full = abs(phase - 0.5)
    if dist_to_new <= window_frac:
        return "new"
    if dist_to_full <= window_frac:
        return "full"
    return "other"


def returns_by_phase(symbol: str, period: str, window_days: float):
    candles = fetch_candles(symbol, "1d", period)
    closes = np.array([c["close"] for c in candles])
    dates = [c["date"] for c in candles]
    rets = closes[1:] / closes[:-1] - 1
    # date the return ENDS on, mirroring probe_day_of_week.py's convention
    labels = np.array([classify(d, window_days) for d in dates[1:]])
    by_label = {k: rets[labels == k] for k in ("new", "full", "other")}
    return by_label, rets


def subset_control(rets: np.ndarray, n: int, draws: int, rng: np.random.Generator):
    out = np.empty(draws)
    idx = np.arange(len(rets))
    for i in range(draws):
        sample = rng.choice(idx, size=n, replace=False)
        out[i] = rets[sample].mean()
    return out


def report(symbol: str, period: str, window_days: float, draws: int, seed: int = 0):
    by_label, rets = returns_by_phase(symbol, period, window_days)
    rng = np.random.default_rng(seed)
    print(f"\n{symbol}: {len(rets)} trading days, {period}; window=±{window_days}d; "
          f"overall mean daily return {rets.mean():.4%}")
    rows = []
    for label in ("new", "full", "other"):
        r = by_label[label]
        n = len(r)
        actual = r.mean()
        null = subset_control(rets, n, draws, rng)
        p = ((np.abs(null) >= abs(actual)).sum() + 1) / (draws + 1)
        print(f"  {label:5s}: n={n} mean={actual:.4%} null_mean={null.mean():.4%} p={p:.4f}")
        rows.append((symbol, label, actual, n, p))
    new_mean = by_label["new"].mean()
    full_mean = by_label["full"].mean()
    print(f"  new - full = {new_mean - full_mean:.4%} "
          f"(literature predicts new > full)")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period-nifty", default="20y")
    parser.add_argument("--period-spx", default="max")
    parser.add_argument("--window-days", type=float, default=3.0)
    parser.add_argument("--draws", type=int, default=5000)
    args = parser.parse_args()

    all_rows = []
    all_rows += report("^NSEI", args.period_nifty, args.window_days, args.draws)
    all_rows += report("^GSPC", args.period_spx, args.window_days, args.draws)

    print("\n=== summary (uncorrected p < 0.05) ===")
    for sym, label, mean, n, p in all_rows:
        if p < 0.05:
            print(f"  {sym} {label}: n={n} mean={mean:.4%} p={p:.4f}")
