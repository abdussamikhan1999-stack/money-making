"""
Probe: the Thirty-seventh entry's overnight-return anomaly
(`OvernightMomentumStrategy`, `--strategy overnight`), screened on a wide
universe FROM THE START - the discipline the Thirty-fifth/Thirty-sixth
entries just learned the hard way (a 10-stock screen's "clean" survivor
collapsed to a chance-level result on a 40-stock retest).

Reuses probe_pead.py's own 40-stock NSE large-cap UNIVERSE directly (no
reason to hand-roll a second one) and backtest_daily.py's existing
simulate_daily/walk_forward_daily/STRATEGIES machinery unchanged - this
probe adds no new strategy logic, only a multi-symbol driver loop plus a
parameter-perturbation sweep, mirroring probe_high52w_widen.py's shape.

Usage:
    python probe_overnight.py --walk-forward
    python probe_overnight.py --quarter-split --symbol TCS.NS
    python probe_overnight.py --perturb --symbol TCS.NS
"""
import argparse

from backtest_daily import STRATEGIES, fetch_daily_yfinance, simulate_daily, walk_forward_daily
from probe_high52w_widen import quarter_split  # reuse, no reason to duplicate this helper
from probe_pead import UNIVERSE

COMMISSION_PER_TRADE = 20.0  # same ~5bps-equivalent round-trip cost used throughout this file


def _args_ns(**overrides):
    """Build an argparse.Namespace with overnight's CLI defaults (see
    backtest_daily.py's --overnight-* args) so STRATEGIES["overnight"] can
    be called the same way the CLI does."""
    ns = argparse.Namespace(overnight_lookback=5, overnight_entry_threshold=0.0015,
                             overnight_stop_atr_multiple=3.0)
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def screen(**kwargs):
    factory = lambda: STRATEGIES["overnight"](_args_ns())
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
        n_trades = len(b1.trade_log) + len(b2.trade_log)
        passed = p1 > 0 and p2 > 0 and not r1.drawdown_halted and not r2.drawdown_halted and n_trades > 0
        results[sym] = (p1, p2, n_trades, passed)
        flag = "PASS" if passed else "fail"
        print(f"{sym:16s} in-sample={p1:>10.1f}  out-of-sample={p2:>10.1f}  trades={n_trades:>4d}  {flag}")
    passers = [s for s, (_, _, _, p) in results.items() if p]
    print(f"\n{len(passers)}/{len(results)} passed walk-forward: {passers}")
    return results


def quarter_check(symbol, **kwargs):
    daily = fetch_daily_yfinance(symbol, "10y")
    factory = lambda: STRATEGIES["overnight"](_args_ns())
    chunks = quarter_split(daily, factory, commission_per_trade=COMMISSION_PER_TRADE, **kwargs)
    pnls = [b.cash_pnl for b, r in chunks]
    print(f"{symbol} quarter pnls: {[round(p, 1) for p in pnls]}")
    print(f"all 4 positive: {all(p > 0 for p in pnls)}")
    return pnls


def perturb(symbol, **kwargs):
    daily = fetch_daily_yfinance(symbol, "10y")
    print(f"--- {symbol}: entry_threshold sweep (lookback=5) ---")
    for thr in (0.0005, 0.001, 0.0015, 0.002, 0.003):
        factory = lambda thr=thr: STRATEGIES["overnight"](_args_ns(overnight_entry_threshold=thr))
        (b1, r1), (b2, r2) = walk_forward_daily(daily, factory, commission_per_trade=COMMISSION_PER_TRADE, **kwargs)
        ok = b1.cash_pnl > 0 and b2.cash_pnl > 0 and not r1.drawdown_halted and not r2.drawdown_halted
        print(f"  entry_threshold={thr:.4f}  is={b1.cash_pnl:>10.1f}  oos={b2.cash_pnl:>10.1f}  {'ok' if ok else 'FAIL'}")
    print(f"--- {symbol}: lookback sweep (entry_threshold=0.0015) ---")
    for lb in (3, 5, 10, 20):
        factory = lambda lb=lb: STRATEGIES["overnight"](_args_ns(overnight_lookback=lb))
        (b1, r1), (b2, r2) = walk_forward_daily(daily, factory, commission_per_trade=COMMISSION_PER_TRADE, **kwargs)
        ok = b1.cash_pnl > 0 and b2.cash_pnl > 0 and not r1.drawdown_halted and not r2.drawdown_halted
        print(f"  lookback={lb:>3d}  is={b1.cash_pnl:>10.1f}  oos={b2.cash_pnl:>10.1f}  {'ok' if ok else 'FAIL'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    parser.add_argument("--perturb", action="store_true")
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()

    if args.quarter_split:
        quarter_check(args.symbol)
    elif args.perturb:
        perturb(args.symbol)
    else:
        screen()
