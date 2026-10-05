"""
Hundred-and-seventh entry: loss attribution for IBS rotation -- not "does a regime overlay
improve Calmar" (Entries 61/76/103-106 already answered that for three overlays, all
unconfirmed cross-universe), but the narrower, more basic question those overlays were built
to act on without ever being asked directly: WHICH regime each of this strategy's existing
120 real monthly outcomes actually fell in, and whether the bad months differ from the good
ones by more than chance.

Reuses IBS(5) top_k=5 lag=1 on WIDE_UNIVERSE (this project's standing finding) unchanged --
no new strategy, no new universe, no new fetch beyond the NIFTY/India-VIX series
probe_macro_analog.py already caches. Four regime variables, each already used somewhere in
this project (trend: Sixty-first/Seventy-sixth entries' gate; realized vol and India VIX
level: the macro-analog/fear-buy entries; breadth: the Hundred-and-first entry's Zweig
probe), computed once at the ranking date of EVERY real month (no lookahead -- same
align_to/rolling convention probe_reversal_rotation.py already uses for its own gate), then
cross-tabulated against the 120 real monthly returns instead of being used to gate anything.

Significance: for each bucket, is the STRATEGY's own mean return in that bucket different
from what buying top_k RANDOM eligible stocks in the exact same months would have earned --
the same random-portfolio-control shape every other test in this file uses, applied here to
ask whether a regime explains the STOCK-PICKING edge's variation (alpha risk) rather than
just plain market beta (every bucket's random control already prices beta in).

Checked on both WIDE_UNIVERSE and UNIVERSE_B (its own breadth; NIFTY/VIX are market-level and
shared) before anything here is read as a finding, per this project's own standing practice.
"""
import argparse

import numpy as np

from probe_reversal_rotation import align_to, load_matrices, scores, simulate, UNIVERSE_B
import probe_reversal_rotation as rr
import probe_macro_analog as pm


def regime_series(M):
    """Trend (close > 150d SMA), realized vol (21d annualized stdev of returns), India VIX
    level, and this universe's own breadth (fraction of stocks above their own 200d SMA) --
    all aligned to M's calendar, all computed from data through that day only."""
    macro = pm.load()
    nifty_full = macro["nifty"].dropna()
    ivix_full = macro["ivix"].dropna()
    nifty = align_to(M, nifty_full)
    sma150 = align_to(M, nifty_full.rolling(150, min_periods=150).mean())
    trend_up = np.where(sma150.isna(), True, (nifty > sma150).to_numpy())
    vol21 = align_to(M, nifty_full.pct_change().rolling(21, min_periods=21).std() * np.sqrt(252))
    ivix = align_to(M, ivix_full)
    c = M["close"]
    breadth = (c > c.rolling(200, min_periods=200).mean()).mean(axis=1).to_numpy()
    return dict(trend_up=trend_up, vol21=vol21.to_numpy(), ivix=ivix.to_numpy(), breadth=breadth)


def tercile_bucket(values, idx):
    """Tercile edges from the full history (not just the sampled idx), so a bucket's
    definition doesn't depend on which months happen to get selected."""
    v = values[idx]
    ok = ~np.isnan(v)
    lo, hi = np.nanpercentile(v[ok], [33.3, 66.7])
    out = np.full(len(v), "mid", dtype=object)
    out[v <= lo] = "low"
    out[v >= hi] = "high"
    out[~ok] = "nan"
    return out


def run(universe_b=False, top_k=5, seeds=1500):
    if universe_b:
        rr.SYMBOLS = UNIVERSE_B
    M = load_matrices()
    rr.SYMBOLS = None
    S = scores(M, "ibs", 5)
    r = simulate(M, S, top_k, 1)
    at = np.array(r["at"])
    months = r["months"]
    reg = regime_series(M)
    label = "UNIVERSE_B" if universe_b else "WIDE_UNIVERSE"
    print(f"\n=== {label}: {len(months)} months, full-period final capital {r['final']:,.0f}, "
          f"max drawdown {r['max_dd']:.1%} ===")

    buckets = {
        "trend": np.where(reg["trend_up"][at], "up", "down"),
        "vol21_tercile": tercile_bucket(reg["vol21"], at),
        "ivix_tercile": tercile_bucket(reg["ivix"], at),
        "breadth_tercile": tercile_bucket(reg["breadth"], at),
    }

    rng = np.random.default_rng(0)
    random_months = np.array([simulate(M, S, top_k, 1, rng=rng)["months"] for _ in range(seeds)])
    # simulate() with rng drops the same trailing months as the real run (len(me)-1 minus the
    # break-on-NaN-price clause), so random_months columns line up positionally with `at`/`months`
    # as long as every random draw has the same length -- checked, not assumed.
    assert random_months.shape[1] == len(months), (random_months.shape[1], len(months))

    for name, labels in buckets.items():
        print(f"\n-- {name} --")
        for val in sorted(set(labels) - {"nan"}):
            mask = labels == val
            n = mask.sum()
            if n < 5:
                print(f"  {val}: n={n}, too few months to read")
                continue
            actual_mean = months[mask].mean()
            actual_worst = months[mask].min()
            random_bucket_means = random_months[:, mask].mean(axis=1)
            p = ((random_bucket_means >= actual_mean).sum() + 1) / (seeds + 1)
            print(f"  {val:5s} n={n:3d}  mean/mo {actual_mean:+.2%}  worst {actual_worst:+.2%}  "
                  f"random mean/mo {random_bucket_means.mean():+.2%}  p(random >= actual)={p:.4f}")

    print(f"\n-- worst 10 of {len(months)} months, with regime state at the ranking date --")
    order = np.argsort(months)[:10]
    for i in order:
        a = at[i]
        print(f"  {M['close'].index[a].date()}  return {months[i]:+.2%}  trend={'up' if reg['trend_up'][a] else 'down'}  "
              f"vol21={reg['vol21'][a]:.1%}  ivix={reg['ivix'][a]:.1f}  breadth={reg['breadth'][a]:.0%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-b", action="store_true")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=1500)
    a = ap.parse_args()
    run(universe_b=a.universe_b, top_k=a.top_k, seeds=a.seeds)


if __name__ == "__main__":
    main()
