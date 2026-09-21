"""
Eighty-third entry: abnormal-trading-volume signals on two disjoint NSE universes and TWO independent periods.
Pre-registered here BEFORE any signal was scored against a return.

Why: the Eighty-second entry tests NSE delivery percentage, but that file only exists from ~2019-09, so it has no
earlier decade to replicate in. Volume is the closest freely available relative with 20 years of history, so it answers
two things at once: (1) is there a "high-volume return premium" (Gervais-Kaniel-Mingelgrin 2001: stocks with unusually
high volume tend to earn higher returns afterwards) on NSE at all, replicated across time and across stocks; (2) if delivery
shows information later, does volume in the same window carry it too. Not tested anywhere in this project (Amihud used
volume as a LEVEL for illiquidity; CMF/OBV are single-instrument trend confirmations, not cross-sectional).

DATA: Yahoo daily close and volume (adjusted), 20y, for UNIV_A (`WIDE_UNIVERSE`, 52) and UNIV_B (`UNIVERSE_B`, 54),
calendar = ^NSEI (never "the stock with the most rows"). Zero-volume days are treated as missing.
SIGNALS at the close of day t:
  V1 = ln( mean(vol, days t-4..t) / mean(vol, days t-64..t-5) ).            High = long.
  V2 = V1 x sign(close_t / close_{t-5} - 1)   (volume surge on a rising stock = accumulation, on a falling one =
       distribution).                                                        High = long.
Direction fixed in advance ("high => higher future return"); a significant negative IC is reported as a reversed
sign, not adopted post hoc.
FORWARD RETURN (lag 1): enter at close t+1, exit at close t+1+h, h in {1, 5, 10, 21}.
STATISTIC and NULL: identical to `probe_delivery_signal.py` (daily cross-sectional rank IC, >= 25 names per row, 4,000
circular shifts >= 60 days of the signal panel against returns, two-sided (count+1)/(n+1)); reported per period:
  P1 = 2019-10-01 .. end (the delivery entry's window, for a head-to-head), P2 = start .. 2019-09-30 (independent
  decade; survivorship is worse here because both universes are today's constituents, a loser-bounce type effect is the
  most inflated by that, a volume-surge-with-momentum effect less so, but every earlier-decade cell is weaker evidence).
REGISTERED: 2 signals x 4 horizons x 2 universes x 2 periods = 32 tests.
DECISION RULE (fixed now): a signal/horizon advances only if, in BOTH universes and BOTH periods, mean IC > 0 with
uncorrected p < 0.05, both halves of each period are positive, and the gross top-5 excess over h days exceeds the 0.25%
round-trip cost. Otherwise: null, or "information without economics". Even an advance faces the honest-family
Bonferroni threshold (m ~ 650) and is only a hypothesis for forward tracking.
POST-REVIEW CORRECTION (independent code review, before the write-up): the 5-day/60-day windows now tolerate missing days
(need 4 and 55). With strict windows one bad Yahoo day (2025-03-18: zero volume for most names) blanked V1 for 65 days,
~4% of P1 and all in its second half, so the halves test rested on fewer days than registered. Loader and runner are
now shared with `probe_delivery_signal.py`.
"""

import argparse
import os

import numpy as np
import pandas as pd

import probe_delivery_signal as ds

_HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = pd.Timestamp("2019-10-01")
PANEL_CACHE = os.path.join(_HERE, "volume_panel_cache.pkl")  # git-ignored: Yahoo history revises


def load_panels(symbols, period="20y"):
    """(close, volume) on the ^NSEI calendar; zero volume -> NaN (shared loader in probe_delivery_signal)."""
    f = ds.load_fields(symbols, ["close", "volume"], period)
    return f["close"], f["volume"]


def signals(vol, close):
    """Row t uses data through t only."""
    # min_periods 4 / 55: one bad Yahoo day (2025-03-18) blanked 65 days of V1 with strict windows (review fix)
    v1 = np.log(vol.rolling(5, min_periods=4).mean() / vol.shift(5).rolling(60, min_periods=55).mean())
    v2 = v1 * np.sign(close / close.shift(5) - 1)
    return {"V1": v1, "V2": v2}


def run(close, vol, seed=1):
    periods = [("P1", close.index >= SPLIT), ("P2", close.index < SPLIT)]
    return ds.run_signals(signals(vol, close), close, ds.HORIZONS, periods, np.random.default_rng(seed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore the local panel cache")
    a = ap.parse_args()
    pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:9.4f}")
    if not a.refresh and os.path.exists(PANEL_CACHE):
        panels = pd.read_pickle(PANEL_CACHE)
    else:
        panels = {n: load_panels(s) for n, s in ds.universes().items()}
        pd.to_pickle(panels, PANEL_CACHE)
    outs = []
    for name, (close, vol) in panels.items():
        print(f"\nuniverse {name}: {close.shape[1]} symbols, {close.index[0].date()}..{close.index[-1].date()}")
        res = run(close, vol)
        res.insert(0, "universe", name)
        print(res.to_string(index=False))
        outs.append(res)
    pd.concat(outs).to_csv(os.path.join(_HERE, "volume_signal_results.csv"), index=False)


if __name__ == "__main__":
    main()
