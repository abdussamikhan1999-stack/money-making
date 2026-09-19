"""
Seventy-ninth entry: Larry Williams' volatility breakout (his 1987 championship-winning rule), tested on daily OHLC.

Pre-registered before running: k in {0.4, 0.6, 0.8}. Rule per day t: trigger = open_t + k * (high_{t-1} - low_{t-1});
if high_t >= trigger, enter LONG at max(open_t, trigger) and exit at close_t. (Optimistic: assumes a fill at the trigger.)
Also the symmetric SHORT (trigger = open_t - k * range, enter at min(open_t, trigger)), reported separately because a
retail cash account cannot short overnight-free in NSE cash but index futures / SPY can.
Instruments: ^NSEI (2007+), NIFTYBEES.NS (2009+), SPY (1993+). Cost 0.05% per leg (0.1% round trip); a second column adds
0.10% slippage per leg. Statistic: mean net return per trade with a day-block bootstrap p (H0: mean <= 0), win rate, trades/yr,
plus the same rule's mean on the FIRST and SECOND half of the sample.
"""

import argparse

import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.0005


def ohlc(t, start):
    d = yf.Ticker(t).history(start=start)[["Open", "High", "Low", "Close"]].dropna()
    d = d[(d["High"] > d["Low"]) & (d["Open"] > 0)]
    d.index = pd.DatetimeIndex(d.index.date)
    return d[~d.index.duplicated()]


def trades(d, k, side, slip=0.0):
    """Net return per triggered day (fractions). Uses only prior-day range and today's open: no look-ahead
    in the trigger; the fill at the trigger price is the optimistic assumption."""
    rng = (d["High"] - d["Low"]).shift(1)
    o, h, l, c = d["Open"], d["High"], d["Low"], d["Close"]
    if side == "long":
        trig = o + k * rng
        hit = h >= trig
        entry = np.maximum(o, trig)
        ret = c / (entry * (1 + slip)) - 1
    else:
        trig = o - k * rng
        hit = l <= trig
        entry = np.minimum(o, trig)
        ret = (entry * (1 - slip)) / c - 1
    ret = (ret - 2 * COST)[hit & rng.notna()]
    return ret


def boot_p(x, n=10000, seed=0):
    rng = np.random.default_rng(seed)
    x = np.asarray(x)
    means = np.array([rng.choice(x, size=len(x), replace=True).mean() for _ in range(n)])
    return ((means <= 0).sum() + 1) / (n + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    for name, tkr, start in (("^NSEI 2007+", "^NSEI", "2007-01-01"), ("SPY 1993+", "SPY", "1993-01-01")):
        # NIFTYBEES.NS is deliberately absent: its Yahoo OHLC is corrupt (rows with Open=High=Low=Close, mean open->close
        # -0.41%/day), which produced a spurious p~0 short result before this guard existed (Seventy-ninth entry).
        d = ohlc(tkr, start)
        bad = abs((d["Close"] / d["Open"] - 1).mean())
        if bad > 0.0015 and tkr != "^NSEI":
            print(f"{name}: skipped, unconditional |open->close| mean {bad:.3%}/day is implausible (bad OHLC)")
            continue
        yrs = (d.index[-1] - d.index[0]).days / 365.25
        print(f"\n{name}: {len(d)} days, {yrs:.1f} years; unconditional open->close mean {(d['Close'] / d['Open'] - 1).mean():+.3%}/day")
        for side in ("long", "short"):
            for k in (0.4, 0.6, 0.8):
                r = trades(d, k, side)
                rs = trades(d, k, side, slip=0.001)
                if len(r) < 30:
                    print(f"  {side:5s} k={k}: only {len(r)} trades")
                    continue
                h = len(r) // 2
                print(f"  {side:5s} k={k}: {len(r):4d} trades ({len(r) / yrs:5.1f}/yr), mean {r.mean():+.3%} net, win {np.mean(r > 0):.0%}, "
                      f"p(mean<=0)={boot_p(r):.3f}; with 0.1% slippage/leg {rs.mean():+.3%} (p={boot_p(rs):.3f}); halves {r.iloc[:h].mean():+.3%}/{r.iloc[h:].mean():+.3%}")


if __name__ == "__main__":
    main()
