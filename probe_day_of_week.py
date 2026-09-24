"""
Probe: the day-of-week ("Monday") effect (French 1980, "Stock returns and
the weekend effect": average Monday returns are historically lower, often
negative, compared to other weekdays - one of the oldest documented
calendar anomalies, widely cited alongside turn-of-month and the
January effect). Genuinely different from every calendar effect already
tested in this project: the Sixth entry's turn-of-month rule is a
MULTI-DAY WINDOW keyed to the calendar month; this is a SINGLE-DAY
classification keyed to the calendar WEEK, with no lookback/lookahead of
any kind - the simplest possible calendar signal, and one this project
has never directly tested despite testing turn-of-month, overnight/
intraday decomposition, and VIX-regime timing in the same entry.

Pre-registered before any return was computed: is any weekday's mean
daily return significantly different from what a random subset of
trading days of the same size would produce? Tested on two independent,
long-history markets - NIFTY (`^NSEI`, since ~2000) and the S&P 500
(`^GSPC`, since ~1970, matching the long-history precedent already set by
the Seventy-ninth/Eighty-first entries) - 5 weekdays x 2 markets = 10
tests. A significant deviation is reported regardless of its sign (not
adopted post hoc only if it matches French's own "Monday is worst"
direction) since the published effect itself has been repeatedly found
to weaken/reverse in more recent decades by other researchers, so this
entry doesn't assume which sign, if any, would show up now.

STATISTIC: mean daily return on weekday W vs the full-sample mean.
NULL: `draws` random same-size subsets of ALL trading days (without
replacement, ignoring calendar structure entirely), computing each
subset's mean - directly analogous to the random-portfolio controls used
throughout this project's cross-sectional rotation entries, applied here
to a random SUBSET OF DAYS instead of a random subset of STOCKS. Two-sided
p = (count(|random mean| >= |actual mean|) + 1) / (draws + 1).
"""
import argparse

import numpy as np

from data_yfinance import fetch_candles


def daily_returns_by_weekday(symbol: str, period: str):
    """(weekday_index 0=Mon..4=Fri) -> numpy array of that weekday's daily
    returns, plus the full return series (for the random-subset null)."""
    candles = fetch_candles(symbol, "1d", period)
    closes = np.array([c["close"] for c in candles])
    dates = [c["date"] for c in candles]
    rets = closes[1:] / closes[:-1] - 1
    weekdays = np.array([d.weekday() for d in dates[1:]])  # weekday of the day the return ENDS on
    by_day = {w: rets[weekdays == w] for w in range(5)}
    return by_day, rets


def subset_control(rets: np.ndarray, n: int, draws: int, rng: np.random.Generator):
    """Mean of `draws` random same-size (n) subsets of `rets`, without replacement each draw."""
    out = np.empty(draws)
    idx = np.arange(len(rets))
    for i in range(draws):
        sample = rng.choice(idx, size=n, replace=False)
        out[i] = rets[sample].mean()
    return out


def report(symbol: str, period: str, draws: int, seed: int = 0):
    by_day, rets = daily_returns_by_weekday(symbol, period)
    rng = np.random.default_rng(seed)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    print(f"\n{symbol}: {len(rets)} trading days, {period}; overall mean daily return {rets.mean():.4%}")
    rows = []
    for w in range(5):
        r = by_day[w]
        n = len(r)
        actual = r.mean()
        null = subset_control(rets, n, draws, rng)
        p = ((np.abs(null) >= abs(actual)).sum() + 1) / (draws + 1)
        pos_frac = (r > 0).mean()
        half = n // 2
        h1, h2 = r[:half].mean(), r[half:].mean()
        print(f"  {names[w]}: n={n} mean={actual:.4%} pos_frac={pos_frac:.1%} "
              f"halves=({h1:.4%},{h2:.4%}) null_mean={null.mean():.4%} p={p:.4f}")
        rows.append((symbol, names[w], actual, p))
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
    for sym, day, mean, p in all_rows:
        if p < 0.05:
            print(f"  {sym} {day}: mean={mean:.4%} p={p:.4f}")
