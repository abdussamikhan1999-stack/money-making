"""
Hundred-and-ninth entry: Alexander's (1961) Filter Rule, and David Aronson's own methodology
("Evidence-Based Technical Analysis") for testing it -- a block-bootstrap data-mining-bias
correction (the same idea behind White's "Reality Check"/Hansen's SPA test, which Aronson's
book cites as the right way to test the BEST of many rules, not just one pre-picked rule).

Genuinely new on two axes this project hasn't combined before: (1) the filter rule itself --
long when price is x% above its own most recent trough (since it last went flat), flat when
x% below its own most recent peak (since it last went long) -- is NOT any of the trend-
following or mean-reversion constructions already tried here (not a fixed-N-day channel like
Donchian, not an ATR-ratchet band like SuperTrend, not a moving-average cross); it is specific
to Alexander's own definition and one of the oldest technical rules academics have tested,
predating almost everything else in this file by decades. (2) The correction: every other
significance test in this project (random-portfolio control, Bonferroni/BH in
multiple_comparisons.py) either tests ONE pre-registered config against a null, or corrects a
family of ALREADY-COMPUTED p-values after the fact. This entry instead asks the harder
question Aronson's book is actually about: if you search a grid of x and report the single best
one, what's the honest p-value of THAT selection process, simulated directly under a null
that preserves local (within-block) serial dependence but destroys the long-range trend
structure a filter rule needs to profit from real price "runs"? A block-bootstrap of daily
returns answers that: resample 21-day blocks of the REAL return series (with replacement) to
build a synthetic price path with the same local autocorrelation but no genuine persistent
long-horizon trend across block boundaries, rerun the WHOLE grid on every synthetic path, and
track the single best cell's performance each time -- the distribution "how good does the BEST
of this grid look on data with no real exploitable trend" is the correction.

No lookahead: the filter rule's state (long/flat) is decided using data through close[i] only;
it is FILLED at close[i+1] (lag 1, this project's standing convention), and the position filled
at close[i+1] earns the return from close[i+1] to close[i+2] -- i.e. the position earning
daily_ret[t] (close[t]/close[t-1]-1) was decided using data through close[t-2]. A synthetic
all-up-then-reversal sanity check (not a pytest file -- standalone probes here don't get one)
confirms the state machine flips long/flat at the right points before any real number is
trusted.

Tested on NIFTY (`^NSEI`, this project's home market) and the S&P 500 (`^GSPC`, 20y -- the same
long-history-cross-check precedent the Seventy-fourth/Ninety-second entries already set), per-
leg cost 0.1% (an ETF-level index-timing cost, the `probe_etf_rotation.py` convention, not this
file's own equity-delivery 0.2%+DP figure -- there is no single underlying "stock" here to pay a
DP charge on). Pre-registered grid: filter % x in {1, 2, 3, 4, 5, 7.5, 10, 15, 20, 25}.
"""
import argparse

import numpy as np

from data_yfinance import fetch_candles

COST_PCT = 0.1
GRID = (1.0, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0, 15.0, 20.0, 25.0)


def fetch_close(symbol, period):
    candles = fetch_candles(symbol, "1d", period)
    return np.array([c["close"] for c in candles], dtype=float)


def filter_signal(close, x_pct):
    """Alexander filter rule state, decided at close[i] using data through close[i] only.
    state[i] = 1 (long) or 0 (flat) AFTER evaluating close[i]."""
    x = x_pct / 100.0
    n = len(close)
    state = np.zeros(n, dtype=np.int8)
    pos = 0
    trough = close[0]
    peak = close[0]
    for i in range(n):
        px = close[i]
        if pos == 0:
            if px < trough:
                trough = px
            if px >= trough * (1 + x):
                pos = 1
                peak = px
        else:
            if px > peak:
                peak = px
            if px <= peak * (1 - x):
                pos = 0
                trough = px
        state[i] = pos
    return state


def backtest(close, x_pct, cost_pct=COST_PCT):
    """Net annualized return, max drawdown, halves, quarters, trade count -- lag-1 fill.
    state[i] is decided using data through close[i], filled at close[i+1]; the position
    filled at close[i+1] is held until the NEXT fill at close[i+2] (state[i+1]'s own fill),
    so it earns daily_ret[i+1] = close[i+2]/close[i+1]-1. Equivalently: active[k] (the
    position earning daily_ret[k] = close[k+1]/close[k]-1) equals state[k-1]. Cost is
    charged on the day a fill changes the active position (active[k] != active[k-1])."""
    n = len(close)
    state = filter_signal(close, x_pct)
    daily_ret = close[1:] / close[:-1] - 1  # daily_ret[k] = close[k+1]/close[k]-1, k=0..n-2
    active = np.zeros(n - 1)
    active[1:] = state[:-2]  # active[k] (earns daily_ret[k]) = state[k-1], k>=1
    flips = np.zeros(n - 1)
    flips[1:] = np.abs(active[1:] - active[:-1])
    net_ret = active * daily_ret - flips * (cost_pct / 100.0)
    return net_ret


def stats(net_ret):
    cum = np.cumprod(1 + net_ret)
    final = cum[-1]
    years = len(net_ret) / 252.0
    cagr = (final ** (1 / years) - 1) * 100 if final > 0 and years > 0 else -100.0
    peak = np.maximum.accumulate(cum)
    dd = ((peak - cum) / peak).max()
    half = len(net_ret) // 2
    h1 = np.prod(1 + net_ret[:half]) - 1
    h2 = np.prod(1 + net_ret[half:]) - 1
    qs = [np.prod(1 + q) - 1 for q in np.array_split(net_ret, 4)]
    return dict(cagr=cagr, dd=dd, final=final, h1=h1, h2=h2, qs=qs)


def sanity_check():
    """Synthetic all-up-then-sharp-reversal series: the rule must go long early and flip
    flat near the top, not stay long through the whole crash. Not a pytest file, per this
    project's own standalone-probe convention."""
    up = 100 * np.cumprod(1 + np.full(60, 0.01))
    down = up[-1] * np.cumprod(1 + np.full(30, -0.02))
    close = np.concatenate([up, down])
    state = filter_signal(close, 5.0)
    assert state[0] == 0, "should be flat on day 0 (no rise yet to compare against)"
    assert state[40] == 1, "should be long well into the uptrend, 5% above the day-0 trough"
    assert state[-1] == 0, "should have flipped flat during the sharp reversal, not ridden it down"
    print("sanity check passed: filter rule goes long in the uptrend, flat in the reversal")


def block_bootstrap_price(close, block=21, rng=None):
    rng = rng or np.random.default_rng()
    daily_ret = close[1:] / close[:-1] - 1
    n = len(daily_ret)
    out = []
    while len(out) < n:
        start = rng.integers(0, n - block + 1) if n > block else 0
        out.extend(daily_ret[start:start + block])
    out = np.array(out[:n])
    synth = close[0] * np.cumprod(1 + out)
    return np.concatenate([[close[0]], synth])


def run(symbol, period, draws, seed=0):
    close = fetch_close(symbol, period)
    print(f"\n=== {symbol}, {period}: {len(close)} trading days ===")
    cells = {}
    for x in GRID:
        net = backtest(close, x)
        s = stats(net)
        n_trades = int(np.abs(np.diff(filter_signal(close, x).astype(int))).sum())
        cells[x] = s
        print(f"  x={x:5.1f}%: {s['cagr']:7.2f}%/yr  maxDD {s['dd']:.1%}  halves {s['h1']:+.0%}/{s['h2']:+.0%}  "
              f"quarters {[f'{q:+.0%}' for q in s['qs']]}  trades={n_trades}")

    best_x = max(cells, key=lambda x: cells[x]["final"])
    best_final = cells[best_x]["final"]
    print(f"  best cell: x={best_x:.1f}% (final capital multiple {best_final:.2f}x)")

    rng = np.random.default_rng(seed)
    null_best = np.empty(draws)
    null_same_x = np.empty(draws)
    for d in range(draws):
        synth = block_bootstrap_price(close, block=21, rng=rng)
        finals = np.array([stats(backtest(synth, x))["final"] for x in GRID])
        null_best[d] = finals.max()
        null_same_x[d] = finals[GRID.index(best_x)]

    p_naive = ((null_same_x >= best_final).sum() + 1) / (draws + 1)
    p_corrected = ((null_best >= best_final).sum() + 1) / (draws + 1)
    print(f"  naive p (best x tested alone against its own null): {p_naive:.4f}")
    print(f"  Reality-Check-corrected p (best-of-{len(GRID)}-cell null, {draws} block-bootstrap draws): {p_corrected:.4f}")
    return best_x, p_naive, p_corrected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=500)
    ap.add_argument("--skip-check", action="store_true")
    a = ap.parse_args()
    if not a.skip_check:
        sanity_check()
    run("^NSEI", "10y", a.draws, seed=0)
    run("^GSPC", "20y", a.draws, seed=1)


if __name__ == "__main__":
    main()
