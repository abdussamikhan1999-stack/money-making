"""
Eighty-fourth entry: OHLC-microstructure cross-sectional signals (overnight vs intraday, MAX) on two disjoint NSE
universes and two independent periods. Pre-registered here BEFORE any of these signals was scored against a return.

Why: the daily rank-IC framework built for Entries 82-83 (`probe_delivery_signal.py`) is validated on this data: as a
positive control the known 5-day reversal shows up at p=0.0005 in both universes in the earlier decade. That makes cheap
new pre-registered signals possible. This entry asks which PART of the price move carries information, and one
lottery-preference anomaly, none of which were tested cross-sectionally in this project (Entry 37 tested a
single-stock overnight momentum trade; Entry 24 tested low volatility, a different quantity from MAX).

DATA: Yahoo adjusted open/close, 20y, UNIV_A (`WIDE_UNIVERSE`, 52) and UNIV_B (`UNIVERSE_B`, 54), calendar ^NSEI.
Entry 79 validated Yahoo NSE stock OHLC against the official bhavcopy.
  ov_t = open_t / close_{t-1} - 1 (overnight), intra_t = close_t / open_t - 1, r_t = close_t / close_{t-1} - 1.
SIGNALS at the close of day t (all use data through t only). Direction fixed in advance as "high => higher future return":
  O20 = mean(ov, 20 days)                     overnight PERSISTENCE (Lou-Polk-Skouras: overnight returns persist)
  G5  = - mean(ov, 5 days)                    overnight-gap REVERSAL (a stock that gapped up recently gives it back)
  I5  = - mean(intra, 5 days)                 intraday-move REVERSAL (a stock that rose intraday recently gives it back)
  MAX = - max(r, last 21 days)                lottery demand (Bali-Cakici-Whitelaw: stocks with extreme past maxima
                                              underperform), so LOW recent max = long
G5 and I5 split the known 5-day reversal into its overnight and intraday halves; whichever half carries it is a
descriptive result, not a claim. A significant IC of the opposite sign is reported as reversed, not adopted.
FORWARD RETURN (lag 1): enter at close t+1, exit at close t+1+h, h in {1, 5, 21}.
STATISTIC and NULL: as in `probe_delivery_signal.py` (daily cross-sectional rank IC, >= 25 names per row, 4,000
circular shifts >= 60 days, two-sided (count+1)/(n+1)), per period P1 = 2019-10-01..end, P2 = ..2019-09-30.
REGISTERED: 4 signals x 3 horizons x 2 universes x 2 periods = 48 tests.
DECISION RULE (fixed now): a signal/horizon advances only if in BOTH universes and BOTH periods the mean IC > 0 with
uncorrected p < 0.05, both halves of each period are positive, and the gross top-5 excess over h days exceeds the 0.25%
round-trip cost. Otherwise null or "information without economics". An advance still faces the honest-family Bonferroni
threshold (m ~ 700) and is only a hypothesis for forward tracking. Earlier-decade cells use today's constituents
(survivorship), so P2 is weaker evidence than P1 for any reversal-type signal.
"""

import argparse
import os

import numpy as np
import pandas as pd

import probe_delivery_signal as ds

_HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = pd.Timestamp("2019-10-01")
HS = (1, 5, 21)
PANEL_CACHE = os.path.join(_HERE, "ohlc_panel_cache.pkl")  # git-ignored: Yahoo history revises


def load_panels(symbols, period="20y"):
    """(open, close) on the ^NSEI calendar (shared loader in probe_delivery_signal)."""
    f = ds.load_fields(symbols, ["open", "close"], period)
    return f["open"], f["close"]


def signals(op, cl):
    """Row t uses data through t only."""
    ov = op / cl.shift(1) - 1
    intra = cl / op - 1
    r = cl / cl.shift(1) - 1
    return {"O20": ov.rolling(20, min_periods=20).mean(),
            "G5": -ov.rolling(5, min_periods=5).mean(),
            "I5": -intra.rolling(5, min_periods=5).mean(),
            "MAX": -r.rolling(21, min_periods=21).max()}


def run(op, cl, seed=1):
    periods = [("P1", cl.index >= SPLIT), ("P2", cl.index < SPLIT)]
    return ds.run_signals(signals(op, cl), cl, HS, periods, np.random.default_rng(seed))


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
    for name, (op, cl) in panels.items():
        print(f"\nuniverse {name}: {cl.shape[1]} symbols, {cl.index[0].date()}..{cl.index[-1].date()}")
        res = run(op, cl)
        res.insert(0, "universe", name)
        print(res.to_string(index=False))
        outs.append(res)
    pd.concat(outs).to_csv(os.path.join(_HERE, "ohlc_signal_results.csv"), index=False)


if __name__ == "__main__":
    main()
