"""
Probe: "round number" psychological price barriers — the "aesthetics"
mechanism. Documented behavioral-finance literature (Sonnemans (2006),
"Price clustering and natural resistance points in the Dutch stock
market"; Bhattacharya, Holden & Jacobsen (2012), "Penny Wise, Dollar
Foolish: Buy-Sell Imbalances On and Around Round Numbers") on round
index/price levels acting as psychological support/resistance: traders
find round numbers more salient/aesthetically preferable as reference
points (round-number limit orders cluster there), which can alter
short-horizon return behavior near those levels independent of any
"real" economic information. This project has never tested a pure
price-level (rather than calendar or cross-sectional) signal before.

SIGNAL: for each trading day, find the nearest "round" level (a multiple
of `--round-step`, e.g. 1000 index points) to that day's close, and
express the distance to it as a fraction of the round-step (0 = sitting
exactly on a round level, 0.5 = exactly halfway between two). A day is
"near" a round number if that fraction is <= `--near-frac` (default
0.10, i.e. within the closest 20% of the interval around each round
level), else "far". This is deliberately the simplest version of the
question (proximity only, not approach-direction/crossing dynamics,
which would need a separate resistance-vs-breakout test — a real
follow-up, not built here: ponytail — flat proximity-to-round-number
only, add approach-direction if this cell shows anything worth chasing).

TEST: next trading day's return conditional on "near" vs "far" a round
level, on two independent long-history markets (NIFTY, S&P 500), same
random-same-size-subset null already used by
`probe_day_of_week.py`/`probe_lunar_cycle.py`. No direction pre-assumed
(round numbers could just as plausibly be a magnet as a barrier);
reported regardless of sign.
"""
import argparse

import numpy as np

from data_yfinance import fetch_candles


def distance_fraction(close: float, round_step: float) -> float:
    """Distance from `close` to the nearest multiple of `round_step`, as a
    fraction of `round_step` (0 = exactly on a round level, 0.5 = exactly
    between two)."""
    remainder = close % round_step
    return min(remainder, round_step - remainder) / round_step


def returns_by_proximity(symbol: str, period: str, round_step: float, near_frac: float):
    candles = fetch_candles(symbol, "1d", period)
    closes = np.array([c["close"] for c in candles])
    rets = closes[1:] / closes[:-1] - 1
    # proximity measured on the day BEFORE the return (predicting the next day's move)
    dist = np.array([distance_fraction(c, round_step) for c in closes[:-1]])
    near_mask = dist <= near_frac
    return {"near": rets[near_mask], "far": rets[~near_mask]}, rets, near_mask.mean()


def subset_control(rets: np.ndarray, n: int, draws: int, rng: np.random.Generator):
    out = np.empty(draws)
    idx = np.arange(len(rets))
    for i in range(draws):
        sample = rng.choice(idx, size=n, replace=False)
        out[i] = rets[sample].mean()
    return out


def report(symbol: str, period: str, round_step: float, near_frac: float, draws: int, seed: int = 0):
    by_label, rets, near_share = returns_by_proximity(symbol, period, round_step, near_frac)
    rng = np.random.default_rng(seed)
    print(f"\n{symbol}: {len(rets)} trading days, {period}; round_step={round_step} "
          f"near_frac<={near_frac}; {near_share:.1%} of days classified 'near'; "
          f"overall mean daily return {rets.mean():.4%}")
    rows = []
    for label in ("near", "far"):
        r = by_label[label]
        n = len(r)
        actual = r.mean()
        null = subset_control(rets, n, draws, rng)
        p = ((np.abs(null) >= abs(actual)).sum() + 1) / (draws + 1)
        print(f"  {label}: n={n} mean={actual:.4%} null_mean={null.mean():.4%} p={p:.4f}")
        rows.append((symbol, label, actual, n, p))
    print(f"  near - far = {by_label['near'].mean() - by_label['far'].mean():.4%}")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period-nifty", default="20y")
    parser.add_argument("--period-spx", default="max")
    parser.add_argument("--round-step-nifty", type=float, default=1000.0)
    parser.add_argument("--round-step-spx", type=float, default=500.0)
    parser.add_argument("--near-frac", type=float, default=0.10)
    parser.add_argument("--draws", type=int, default=5000)
    args = parser.parse_args()

    all_rows = []
    all_rows += report("^NSEI", args.period_nifty, args.round_step_nifty, args.near_frac, args.draws)
    all_rows += report("^GSPC", args.period_spx, args.round_step_spx, args.near_frac, args.draws)

    print("\n=== summary (uncorrected p < 0.05) ===")
    for sym, label, mean, n, p in all_rows:
        if p < 0.05:
            print(f"  {sym} {label}: n={n} mean={mean:.4%} p={p:.4f}")
