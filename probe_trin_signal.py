"""
Hundred-and-tenth entry: the Arms Index / TRIN (Richard Arms, 1967; "Short-Term Trading
Index" -- discussed among the classic breadth-based contrarian timing tools in the technical-
analysis literature Aronson's book audits), on the two NSE universes this project already uses
for breadth, cross-checked against each other before trusting anything.

Genuinely different construction from the Hundred-and-first entry's Zweig Breadth Thrust: Zweig
used advance/decline COUNT alone; TRIN divides the advance/decline count ratio by the
advance/decline VOLUME ratio -- (adv_n/dec_n) / (adv_vol/dec_vol) -- so it specifically asks
whether the VOLUME behind the day's advancers/decliners is proportionate to how many of them
there are. A high TRIN (volume piling disproportionately into the FEW declining names) is read,
in the classic literature, as a short-term oversold/contrarian-bullish extreme; a low TRIN the
opposite. This project has never combined cross-sectional volume with cross-sectional breadth
before (Zweig's breadth used counts only; the Sixteenth/Eighty-third entries' volume signals were
single-stock, not aggregated across a universe).

No lookahead: TRIN is smoothed over a 10-day trailing window (the standard practical convention
-- raw single-day TRIN is extremely noisy, and "10-day TRIN" is itself a named variant in the
Arms literature, not a parameter chosen to flatter this test) using only data through the
decision day's close, tercile-split on the FULL history (so a bucket's definition doesn't depend
on which days land in it), and tested against NIFTY's forward return with a lag-1 fill (enter the
close AFTER the decision day, this project's standing no-lookahead convention) at h in
{1, 5, 10, 21} trading days. Null: `draws` random same-size subsets of all eligible decision
days, the same random-subset-of-days control used throughout the calendar-effect entries
(Ninety-second/Ninety-eighth).
"""
import argparse

import numpy as np
import pandas as pd

from probe_ibs_rotation_widen import build_wide_price_series, WIDE_UNIVERSE
from probe_ibs_rotation import fetch_calendar
from probe_reversal_rotation import UNIVERSE_B
import probe_macro_analog as pm


def load_close_volume(symbols, period="20y"):
    """UNIVERSE_B's own symbols are bare (no .NS suffix, see probe_reversal_rotation.py's
    load_matrices, which appends it for SYMBOLS=UNIVERSE_B); WIDE_UNIVERSE's are already
    suffixed (probe_pead.UNIVERSE's own convention). Append only when missing."""
    from data_yfinance import fetch_candles
    series = {}
    for sym in symbols:
        ticker = sym if sym.endswith(".NS") else sym + ".NS"
        candles = []
        for _ in range(3):
            candles = fetch_candles(ticker, "1d", period)
            if candles:
                break
        if candles:
            series[sym] = candles
        else:
            print(f"{sym}: fetch failed, excluded")
    cal = fetch_calendar(period)
    idx = pd.DatetimeIndex([c["date"].date() for c in cal])
    close = pd.DataFrame({s: pd.Series([c["close"] for c in cds],
                                        index=pd.DatetimeIndex([c["date"].date() for c in cds]))
                           for s, cds in series.items()}).reindex(idx).ffill()
    vol = pd.DataFrame({s: pd.Series([c["volume"] for c in cds],
                                      index=pd.DatetimeIndex([c["date"].date() for c in cds]))
                         for s, cds in series.items()}).reindex(idx).ffill()
    return close, vol


def trin_series(close, vol, smooth=10):
    """Daily TRIN = (advances/declines) / (advancing volume/declining volume), unchanged
    names excluded from both ratios, then a `smooth`-day trailing mean (the standard "10-day
    TRIN" practical convention -- raw single-day TRIN is too noisy to read directly)."""
    ret = close.pct_change()
    adv, dec = ret > 0, ret < 0
    adv_n, dec_n = adv.sum(axis=1), dec.sum(axis=1)
    adv_vol = vol.where(adv, 0.0).sum(axis=1)
    dec_vol = vol.where(dec, 0.0).sum(axis=1)
    count_ratio = adv_n / dec_n.replace(0, np.nan)
    vol_ratio = (adv_vol / dec_vol.replace(0, np.nan)).replace(0, np.nan)
    raw = count_ratio / vol_ratio
    return raw.rolling(smooth, min_periods=smooth).mean()


def tercile_bucket(values):
    ok = ~values.isna()
    lo, hi = np.nanpercentile(values[ok], [33.3, 66.7])
    out = pd.Series(np.full(len(values), "mid", dtype=object), index=values.index)
    out[values <= lo] = "low"
    out[values >= hi] = "high"
    out[~ok] = "nan"
    return out


def random_subset_p(fwd, n, draws, rng):
    """p = fraction of `draws` random same-size subsets of ALL eligible `fwd` observations
    whose mean is >= the actual bucket mean -- same shape as probe_day_of_week's subset
    control, applied here to forward-return observations instead of single-day returns."""
    idx = np.arange(len(fwd))
    out = np.empty(draws)
    for d in range(draws):
        sample = rng.choice(idx, size=n, replace=False)
        out[d] = fwd[sample].mean()
    return out


def run(symbols, label, period, horizons=(1, 5, 10, 21), draws=2000, seed=0):
    close, vol = load_close_volume(symbols, period)
    trin = trin_series(close, vol)
    nifty_full = pm.load()["nifty"].dropna()
    nifty = nifty_full.reindex(close.index.union(nifty_full.index)).ffill().reindex(close.index)
    nifty_c = nifty.to_numpy()
    bucket = tercile_bucket(trin)
    print(f"\n=== TRIN, {label}, {period}: {trin.notna().sum()} valid days ===")
    rng = np.random.default_rng(seed)
    for h in horizons:
        fwd_all = nifty_c[h + 1:] / nifty_c[1:len(nifty_c) - h] - 1  # lag-1 fill, enter close[t+1]
        print(f"\n-- h={h} --  (overall mean fwd return {np.nanmean(fwd_all):+.3%})")
        for val in ("low", "mid", "high"):
            mask = bucket.to_numpy()[1:len(nifty_c) - h] == val
            mask = mask & ~np.isnan(fwd_all)
            n = mask.sum()
            if n < 20:
                print(f"  {val}: n={n}, too few")
                continue
            actual = fwd_all[mask].mean()
            ok_all = ~np.isnan(fwd_all)
            null = random_subset_p(fwd_all[ok_all], n, draws, rng)
            p = ((np.abs(null) >= abs(actual)).sum() + 1) / (draws + 1)
            print(f"  {val:4s} n={n:4d}  mean fwd {actual:+.3%}  random mean {null.mean():+.3%}  "
                  f"two-sided p={p:.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-b", action="store_true")
    ap.add_argument("--period", default="20y")
    ap.add_argument("--draws", type=int, default=2000)
    a = ap.parse_args()
    if a.universe_b:
        run(UNIVERSE_B, "UNIVERSE_B", a.period, draws=a.draws)
    else:
        run(WIDE_UNIVERSE, "WIDE_UNIVERSE", a.period, draws=a.draws)


if __name__ == "__main__":
    main()
