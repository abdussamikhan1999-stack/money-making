"""
Probe: widen-and-check for the Thirty-fifth entry's 52-week-high proximity
momentum finding (`FiftyTwoWeekHighStrategy`, `--strategy high52w`).

That entry screened 10 instruments and found 4 passers, with `TCS.NS`
genuinely clean on quarter-split (all 4 quarters positive) and `GC=F`
matching this project's established Q4-favorable single-instrument
pattern. Per this project's own established practice (see the Thirtieth/
Thirty-first PEAD entries: a clean 20-stock result fell apart into
reshuffled "winners" on a 40-stock retest), a 2-clean-out-of-10 screen
must be widened before it counts as "found" rather than a lone survivor.

Reuses probe_pead.py's own 40-stock NSE large-cap UNIVERSE directly (no
reason to hand-roll a second one) and backtest_daily.py's existing
simulate_daily/walk_forward_daily/STRATEGIES machinery unchanged - this
probe adds no new strategy logic, only a multi-symbol driver loop.

Usage:
    python probe_high52w_widen.py --walk-forward
    python probe_high52w_widen.py --quarter-split --symbol TCS.NS
"""
import argparse

from backtest_daily import STRATEGIES, fetch_daily_yfinance, simulate_daily, walk_forward_daily
from backtest import split_by_date
from probe_pead import UNIVERSE

COMMISSION_PER_TRADE = 20.0  # same ~5bps-equivalent round-trip cost as the Thirty-fifth entry's screen


def _args_ns(**overrides):
    """Build an argparse.Namespace with high52w's CLI defaults (see
    backtest_daily.py's --high52w-* args) so STRATEGIES["high52w"] can be
    called the same way the CLI does."""
    ns = argparse.Namespace(
        high52w_lookback=252, high52w_entry_threshold=0.95,
        high52w_exit_threshold=0.85, high52w_stop_atr_multiple=3.0,
        high52w_max_hold_days=60,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def quarter_split(daily, strategy_factory, **kwargs):
    dates = sorted({c["date"].date() for c in daily})
    n = len(dates)
    chunk = max(1, n // 4)
    boundaries = [dates[i] for i in range(0, n, chunk)][:5]
    chunks = []
    for i in range(4):
        lo, hi = boundaries[i], (boundaries[i + 1] if i + 1 < len(boundaries) else dates[-1])
        c = [bar for bar in daily if lo <= bar["date"].date() <= hi]
        chunks.append(c)
    return [simulate_daily(c, strategy_factory(), **kwargs) for c in chunks]


def screen(**kwargs):
    factory = lambda: STRATEGIES["high52w"](_args_ns())
    results = {}
    for sym in UNIVERSE:
        try:
            daily = fetch_daily_yfinance(sym, "10y")
        except Exception as e:
            print(f"{sym}: fetch failed ({e})")
            continue
        if len(daily) < 300:
            print(f"{sym}: insufficient data ({len(daily)} bars), skipped")
            continue
        (b1, r1), (b2, r2) = walk_forward_daily(daily, factory, commission_per_trade=COMMISSION_PER_TRADE, **kwargs)
        p1, p2 = b1.cash_pnl, b2.cash_pnl
        passed = p1 > 0 and p2 > 0 and not r1.drawdown_halted and not r2.drawdown_halted
        results[sym] = (p1, p2, passed)
        flag = "PASS" if passed else "fail"
        print(f"{sym:16s} in-sample={p1:>10.1f}  out-of-sample={p2:>10.1f}  {flag}")
    passers = [s for s, (_, _, p) in results.items() if p]
    print(f"\n{len(passers)}/{len(results)} passed walk-forward: {passers}")
    return results


def quarter_check(symbol, **kwargs):
    daily = fetch_daily_yfinance(symbol, "10y")
    factory = lambda: STRATEGIES["high52w"](_args_ns())
    chunks = quarter_split(daily, factory, commission_per_trade=COMMISSION_PER_TRADE, **kwargs)
    pnls = [b.cash_pnl for b, r in chunks]
    print(f"{symbol} quarter pnls: {[round(p, 1) for p in pnls]}")
    print(f"all 4 positive: {all(p > 0 for p in pnls)}")
    return pnls


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()

    if args.quarter_split:
        quarter_check(args.symbol)
    else:
        screen()
