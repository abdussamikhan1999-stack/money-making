"""
Probe: does gold (`GC=F`) pass this project's walk-forward screen more
often than chance would predict given only its OWN volatility/return
distribution, with no genuine day-to-day temporal structure at all?

Direct follow-up on the open question the Ninetieth/Ninety-first entries
named but didn't answer: gold has now been the (often lone) survivor of
FOUR independent, unrelated technical mechanisms in this project (IBS -
Thirteenth entry; CMF+OBV volume confirmation - Sixteenth; Parabolic SAR
- Eighty-ninth; DMI/ADX - Ninetieth), while the DMI/ADX survivor's own
retest against 8 more commodities/FX pairs (Ninety-first) found ZERO
corroboration from gold's own instrument class. Two explanations remain
open: (a) gold's real price series has some genuine, if elusive,
temporal structure several unrelated technical rules can each partially
latch onto, or (b) gold's own volatility/return-distribution PROPERTIES
(not its temporal ordering) make a "both walk-forward halves positive"
screen easier to pass by chance on this specific instrument, independent
of whether any real structure exists - e.g. lower realized volatility
than silver/oil/FX means fewer drawdown-halts, which mechanically raises
the odds of completing a full backtest half with a positive sign by pure
luck. This entry is a scrambled-time control built to separate the two.

METHOD: for each real trading day t (t>=1), compute its OHLCV bar
relative to the PRECEDING real day's close - (open/prev_close,
high/prev_close, low/prev_close, close/prev_close, volume) - a "shape"
tuple that captures that day's own gap and intraday range exactly as it
really happened, with no reference to calendar time. For each of `draws`
random shuffles, permute the ORDER of these shape tuples (day 0's real
close is always the starting point) and re-multiply them out into a new
synthetic OHLCV series, day by day, exactly reproducing each real day's
own gap/range/volume "shape" just in a randomly reordered sequence - a
day-level block bootstrap, the same IID-resampling idea already used
throughout this project's cross-sectional "random portfolio" controls
(Thirty-ninth entry onward), applied here to the ORDER OF TIME on a
single instrument instead of to WHICH STOCKS are picked each month. This
preserves gold's exact marginal volatility/gap/volume distribution and
destroys any genuine serial dependency (trend persistence, mean
reversion, autocorrelation) a real trading rule would need to find.

Four mechanisms run against each shuffle, reusing every one's own
already-reviewed, already-tested simulate/walk-forward code unchanged
(no strategy logic is reimplemented here): `probe_ibs.walk_forward`,
`backtest_daily.walk_forward_daily` with `VolumeConfirmationStrategy`
(CMF+OBV), `probe_parabolic_sar.walk_forward`, `probe_dmi_adx.walk_forward`
- the four mechanisms each entry above found `GC=F` a survivor on, AS OF
THAT ENTRY's data snapshot (this project's own established rolling-window
drift means a fresh fetch can differ - checked directly before trusting
anything below, see the write-up). "Pass" = both walk-forward halves
positive, this project's own standing screening bar throughout.

CAVEAT ON INTERPRETING A SINGLE real-vs-scrambled COMPARISON (found via a
synthetic check before trusting the real result): injecting a genuine,
strong, zero-mean REGIME-SWITCHING signal (alternating 60-day blocks of
opposite strong drift - real serial structure a trend-follower can
exploit, with no overall mean-drift artifact) into synthetic data still
produced a real-vs-scrambled pass-rate gap too NOISY to treat one
real/scrambled comparison alone as a clean test (scrambled pass rates of
15-40% even with real, if imperfectly exploitable, momentum injected) -
a "both halves positive" walk-forward screen has a meaningfully high
chance-level base rate on its own, exactly the phenomenon this project
has called "chance-level hit rate" throughout (16-33% across many
instruments for various strategies). The methodologically sound
comparison this entry actually relies on is therefore BASE-RATE MATCHING:
whether gold's own scrambled pass rate is close to (rather than far
below) the ORIGINAL cross-instrument screening hit rate each mechanism's
own entry reported - not whether real gold "beats" its scrambled
distribution in one draw.
"""
import argparse

import numpy as np

from data_yfinance import fetch_candles


def shape_tuples(daily: list[dict]):
    """One (o_ratio, h_ratio, l_ratio, c_ratio, volume) tuple per day from
    day 1 onward, each ratio relative to the PRECEDING real day's close."""
    out = []
    for i in range(1, len(daily)):
        prev_close = daily[i - 1]["close"]
        b = daily[i]
        out.append((b["open"] / prev_close, b["high"] / prev_close, b["low"] / prev_close,
                    b["close"] / prev_close, b["volume"]))
    return out


def build_shuffled(daily: list[dict], shapes: list[tuple], rng: np.random.Generator):
    order = rng.permutation(len(shapes))
    prev_close = daily[0]["close"]
    out = [dict(date=daily[0]["date"], open=daily[0]["open"], high=daily[0]["high"],
                low=daily[0]["low"], close=daily[0]["close"], volume=daily[0]["volume"])]
    for k, idx in enumerate(order, start=1):
        o_r, h_r, l_r, c_r, vol = shapes[idx]
        close = prev_close * c_r
        out.append(dict(date=daily[k]["date"], open=prev_close * o_r, high=prev_close * h_r,
                         low=prev_close * l_r, close=close, volume=vol))
        prev_close = close
    return out


def run_all(daily: list[dict]):
    """Returns dict of mechanism -> True/False (both walk-forward halves positive)."""
    import probe_ibs
    import probe_parabolic_sar
    import probe_dmi_adx
    import backtest_daily
    from daily_strategy import VolumeConfirmationStrategy

    results = {}

    in_s, out_s = probe_ibs.walk_forward(daily)
    results["ibs"] = (in_s["total_net"] > 0) and (out_s["total_net"] > 0)

    in_s, out_s = probe_parabolic_sar.walk_forward(daily)
    results["sar"] = (in_s["total_net"] > 0) and (out_s["total_net"] > 0)

    in_s, out_s = probe_dmi_adx.walk_forward(daily)
    results["dmi_adx"] = (in_s["total_net"] > 0) and (out_s["total_net"] > 0)

    (b1, r1), (b2, r2) = backtest_daily.walk_forward_daily(daily, VolumeConfirmationStrategy)
    results["cmf_obv"] = (b1.cash_pnl > 0) and (b2.cash_pnl > 0)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="GC=F")
    parser.add_argument("--period", default="10y")
    parser.add_argument("--draws", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    daily = fetch_candles(args.symbol, "1d", args.period)
    print(f"{args.symbol}: {len(daily)} real trading days, {args.period}")

    real = run_all(daily)
    print(f"\nREAL {args.symbol}: {real}")

    shapes = shape_tuples(daily)
    rng = np.random.default_rng(args.seed)
    counts = {k: 0 for k in real}
    for d in range(args.draws):
        synth = build_shuffled(daily, shapes, rng)
        r = run_all(synth)
        for k, v in r.items():
            counts[k] += int(v)

    print(f"\nscrambled-time control ({args.draws} shuffles), fraction of draws that pass "
          f"'both halves positive':")
    for k in real:
        frac = counts[k] / args.draws
        print(f"  {k}: real={real[k]}  scrambled_pass_rate={frac:.1%}  "
              f"p(scrambled passes)={frac:.4f}")
