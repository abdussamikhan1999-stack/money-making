"""
Probe: the classic Stochastic Oscillator (George Lane, 1950s-70s) as a
mean-reversion trigger — sourced from the widely-published, textbook rule
(not tied to one specific GitHub repo the way Squeeze/SuperTrend/volume
were, since this indicator predates any single reference implementation
and every major platform ships the identical formula).

Genuinely different construction from every mean-reversion strategy
already in this project:
  - IBS (`probe_ibs.py`) is a SAME-DAY positional signal: where today's
    close sits within TODAY's own high-low range, no lookback window.
  - RSI(2) (`ConnorsRSI2Strategy`) smooths UP/DOWN move magnitudes with
    Wilder's own exponential-style average over a short window.
  - Bollinger Bands (`BollingerBandsStrategy`) bands the CLOSE series
    itself with a rolling mean +/- standard deviation.
  Stochastic %K bands today's close within the highest-high/lowest-low
  RANGE of the last `k_period` days (not a statistical band, not a
  same-day-only read, not an up/down-move ratio) - a genuinely different
  normalization. %D is a short SMA of %K, the classic "signal line"
  smoothing every stochastic implementation uses.

Formula (day i, window includes day i itself - a stochastic reading is
inherently about where TODAY's close sits in the recent range, the same
same-day-inclusive convention IBS already uses in this project, and the
reason this is a standalone probe rather than a `daily_strategy.py`
strategy: `check_entry(close)` only receives today's CLOSE, not today's
own high/low, which %K's own highest-high/lowest-low window needs -
identical shape-mismatch reasoning already applied to IBS, SuperTrend,
gap-fill, momentum/pairs rotation):
  %K_i = (close_i - lowest(lows, k_period)) / (highest(highs, k_period) - lowest(lows, k_period)) * 100
Reuses `indicators.highest`/`indicators.lowest` directly (already in this
project for Donchian/Turtle Soup/Squeeze/52-week-high) rather than
recomputing a rolling max/min from scratch. Classic implementations also
smooth %K into a %D signal line (a short SMA of %K) and trade the
%K/%D crossover; this probe uses the plainer, more-published %K-only
threshold rule instead - the same "try the simplest textbook version
first" choice already made for RSI-2/Bollinger/IBS rather than a
crossover variant, so %D is not computed at all here.

RULE: long when %K drops below `entry_threshold` (default 20, the
standard "oversold" line), exit when %K climbs back to >= `exit_threshold`
(default 80) or `max_hold_days` times out. Short side (%K above
100-entry_threshold, i.e. "overbought") is this project's own symmetric
extension, not itself literature-backed - the same caveat already
attached to IBS's/volume's/52-week-high's short sides. No natural
structural stop exists for a range-position oscillator (the same
situation RSI-2/Squeeze/Bollinger/MACD were in), so the initial stop is
`stop_atr_multiple` x ATR, this project's now-standard convention for
that case - checked against the day's own high/low (not just its close),
the same "an intraday stop breach isn't missed just because the close
recovered" convention `backtest_daily.py`'s engine already uses, since
this standalone loop doesn't route through that engine. An exit and a
fresh entry are mutually exclusive within the same bar (the position
state is read once per bar), the same no-same-bar-reentry convention
`probe_ibs.py` already uses - independent review of the first version of
this file found this was not the case with early-drafted exit/entry code
that used two independent `if`s instead, letting a long revert (%K>=80)
immediately re-open as a short (%K>80-entry_threshold) on the identical
bar; fixed before any number below was trusted.
"""
import argparse

from indicators import highest, lowest, average_true_range


def stochastic_series(daily: list[dict], k_period: int = 14):
    """One %K value per day - None before the window fills. Computed only
    from days <= i (no lookahead), safe to precompute for the whole series
    up front."""
    n = len(daily)
    highs = [b["high"] for b in daily]
    lows = [b["low"] for b in daily]
    closes = [b["close"] for b in daily]
    ks = [None] * n
    for i in range(k_period - 1, n):
        hh = highest(highs[:i + 1], k_period)
        ll = lowest(lows[:i + 1], k_period)
        if hh is None or ll is None or hh == ll:
            continue
        ks[i] = (closes[i] - ll) / (hh - ll) * 100
    return ks


def simulate_stochastic(daily: list[dict], k_period: int = 14,
                         entry_threshold: float = 20.0, exit_threshold: float = 80.0,
                         stop_atr_multiple: float = 2.0, atr_period: int = 14, max_hold_days: int = 20,
                         capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                         commission_per_trade: float = 20.0, max_drawdown_pct: float = 10.0,
                         enable_short: bool = True):
    ks = stochastic_series(daily, k_period=k_period)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None

    for i in range(atr_period + 1, len(daily)):
        if halted:
            break
        k = ks[i]
        high, low, close = daily[i]["high"], daily[i]["low"], daily[i]["close"]

        if position is not None:
            position["days_in_trade"] += 1
            side = position["side"]
            # intrabar stop first (checked against high/low, not just the close, so a breach
            # that recovers by the close isn't silently missed - backtest_daily.py's own
            # convention for exactly this reason) - mutually exclusive with a fresh entry below
            stopped = (side == "long" and low <= position["stop"]) or \
                      (side == "short" and high >= position["stop"])
            exit_price = position["stop"] if stopped else close
            reverted = (not stopped) and ((side == "long" and k is not None and k >= exit_threshold) or
                                           (side == "short" and k is not None and k <= 100 - exit_threshold))
            timed_out = (not stopped) and (not reverted) and position["days_in_trade"] >= max_hold_days
            if stopped or reverted or timed_out:
                gross = (exit_price - position["entry"]) * position["qty"] if side == "long" \
                    else (position["entry"] - exit_price) * position["qty"]
                net = gross - commission_per_trade
                capital_track += net
                peak = max(peak, capital_track)
                dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if max_dd >= max_drawdown_pct:
                    halted = True
                outcome = "stop" if stopped else ("reverted" if reverted else "timeout")
                trades.append(dict(date=daily[i]["date"], side=side, outcome=outcome,
                                    gross_pnl=gross, net_pnl=net))
                position = None
        elif not halted and k is not None:
            atr = average_true_range(daily[i - atr_period:i + 1], period=atr_period)
            if atr is None or atr <= 0:
                continue
            side = None
            if k < entry_threshold:
                side = "long"
                stop = close - stop_atr_multiple * atr
            elif enable_short and k > 100 - entry_threshold:
                side = "short"
                stop = close + stop_atr_multiple * atr
            if side is not None:
                risk_per_unit = abs(close - stop)
                if risk_per_unit > 0:
                    qty = (capital_track * risk_per_trade_pct / 100) / risk_per_unit
                    if qty > 0:
                        position = dict(side=side, entry=close, stop=stop, qty=qty, days_in_trade=0)

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    return dict(trades=trades, final_capital=capital_track, total_gross=total_gross,
                total_net=total_net, max_dd=max_dd, halted=halted, n_trades=len(trades))


def annualized_return_pct(result, capital, years):
    if years <= 0:
        return 0.0
    total_return = result["total_net"] / capital
    if total_return <= -1:
        return -100.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def _report(label, result, capital, years):
    r = result
    ann = annualized_return_pct(r, capital, years)
    print(f"{label}: trades={r['n_trades']} gross={r['total_gross']:.0f} net={r['total_net']:.0f} "
          f"final_cap={r['final_capital']:.0f} max_dd={r['max_dd']:.1f}% "
          f"annualized={ann:.2f}%/yr halted={r['halted']}")


def walk_forward(daily, split_ratio=0.5, **kwargs):
    n = len(daily)
    cutoff = max(1, int(n * split_ratio))
    before, after = daily[:cutoff], daily[cutoff:]
    return simulate_stochastic(before, **kwargs), simulate_stochastic(after, **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate_stochastic(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--k-period", type=int, default=14)
    parser.add_argument("--entry-threshold", type=float, default=20.0)
    parser.add_argument("--exit-threshold", type=float, default=80.0)
    parser.add_argument("--stop-atr-multiple", type=float, default=2.0)
    parser.add_argument("--max-hold-days", type=int, default=20)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--no-short", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(k_period=args.k_period, entry_threshold=args.entry_threshold,
                  exit_threshold=args.exit_threshold, stop_atr_multiple=args.stop_atr_multiple,
                  max_hold_days=args.max_hold_days, capital=args.capital,
                  risk_per_trade_pct=args.risk_per_trade_pct, commission_per_trade=args.commission_per_trade,
                  enable_short=not args.no_short)
    span_days = (daily[-1]["date"] - daily[0]["date"]).days
    years = span_days / 365.25

    if args.walk_forward:
        in_s, out_s = walk_forward(daily, **kwargs)
        _report("In-sample  (first half)", in_s, args.capital, years / 2)
        _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2)
        consistent = (in_s["total_net"] > 0) == (out_s["total_net"] > 0)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    elif args.quarter_split:
        chunks = quarter_split(daily, **kwargs)
        for i, c in enumerate(chunks):
            _report(f"Q{i+1}", c, args.capital, years / 4)
    else:
        result = simulate_stochastic(daily, **kwargs)
        _report("Full period", result, args.capital, years)
