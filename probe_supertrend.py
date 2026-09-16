"""
Probe: SuperTrend (ATR trailing-band trend-following), reproducing the
widely-cited, widely-forked open-source implementation at
Nikhil-Adithyan/Algorithmic-Trading-with-SuperTrend-Indicator-in-Python
(github.com) — sourced from real strategy CODE again, per the same
standard TTM Squeeze and the CMF+OBV volume strategy were sourced under
(see CLAUDE.md's Fifteenth/Sixteenth entries). Genuinely different
construction from DonchianBreakoutStrategy (already tried, this project's
other trend-following mechanism): Donchian's channel is a FIXED N-day
high/low; SuperTrend's band RATCHETS one direction at a time (only moves
in the trend's favor, never against it, until price actually crosses it) —
a smoother, stickier trailing stop rather than a hard rolling channel.

Algorithm (from the reference's `get_supertrend()`):
  atr = ATR(period)      # source uses an EWMA-smoothed ATR (tr.ewm(...));
                          # this uses this project's own plain/unsmoothed
                          # rolling-average ATR instead (the SAME one every
                          # other ATR-based strategy here already uses —
                          # RSI-2, 3-bar breakout, Squeeze, volume) rather
                          # than adding a second smoothing method. SuperTrend's
                          # mechanism is the ratchet/flip band STRUCTURE, not
                          # the exact ATR smoothing, so this isn't expected
                          # to change the qualitative result.
  hl_avg = (high + low) / 2
  basic_upper, basic_lower = hl_avg +/- multiplier * atr
  final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i-1] or
                    close[i-1] > final_upper[i-1]) else final_upper[i-1]
  final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i-1] or
                    close[i-1] < final_lower[i-1]) else final_lower[i-1]
  The active line flips from the upper band (downtrend) to the lower band
  (uptrend) the first day close crosses above the currently-active upper
  band, and vice versa — a sticky one-way ratchet, not recomputed from
  scratch each day. First valid bar bootstraps as DOWNTREND (upper band
  active) — matching the reference's own actual behavior (its
  `supertrend`/`final_bands` arrays both default to 0 on day 0, and 0==0
  resolves to the upper-band branch, which is checked first in its
  if/elif chain), rather than a "which side is price already on"
  heuristic — that heuristic turned out to be a near-always-true
  tautology on the very first bar (the basic bands straddle price widely
  by construction), which silently forced every series to bootstrap
  "uptrend" regardless of actual direction. Caught by a synthetic
  all-down-days sanity check before trusting any real backtest.

Entry: buy when the trend flips downtrend -> uptrend (close crosses above
the active band); short when it flips uptrend -> downtrend. The reference
strategy is long/flat only — the SHORT side here is this project's own
symmetric extension, same caveat already applied to IBS's/volume's short
sides (not itself source-backed).

Not ported into daily_strategy.py's push/check_entry/check_exit interface:
check_entry(close) only receives TODAY's close, but SuperTrend's own band
for day i is itself a function of day i's own high/low (that's how the
indicator is actually defined and traded — it reacts to the CURRENT bar,
not just yesterday's). Same shape-mismatch reasoning as IBS/gap-fill/
momentum-rotation/pairs-trading — own loop instead of distorting the
shared interface.

Exit: the SuperTrend flip itself IS the exit signal (opposite-direction
flip closes the position) — no separate exit rule needed, unlike RSI-2/
Squeeze/IBS/volume which all needed one bolted on, and no intrabar stop
check either (this is a close-based, end-of-day system, same as the
source). A max_hold_days safety cap is still added for consistency with
every other strategy in this project (a strict trend-follower can
otherwise sit in one position indefinitely). The entry stop for POSITION
SIZING purposes is the active band value itself at entry time (the exact
level price just crossed) — a natural structural stop, the same role
Donchian's own channel plays for its strategy.
"""
import argparse


def _true_range(prev_close, high, low):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def supertrend_series(daily: list[dict], period: int = 10, multiplier: float = 3.0):
    """One (trend, value) pair per day in `daily` — trend is True (uptrend,
    lower band active), False (downtrend, upper band active), or None
    before the ATR window fills. Computed once, up front, for the whole
    series — safe because day i's band only ever depends on days <= i,
    never the future, so there's no lookahead risk in precomputing it."""
    n = len(daily)
    out = [(None, None)] * n
    final_upper = final_lower = None

    for i in range(period, n):
        window = daily[i - period:i + 1]
        trs = [_true_range(window[j - 1]["close"], window[j]["high"], window[j]["low"])
               for j in range(1, len(window))]
        atr = sum(trs) / len(trs)
        hl_avg = (daily[i]["high"] + daily[i]["low"]) / 2
        basic_upper = hl_avg + multiplier * atr
        basic_lower = hl_avg - multiplier * atr
        close = daily[i]["close"]
        prev_close = daily[i - 1]["close"]

        if final_upper is None:  # first valid bar - bootstrap straight from the basic bands
            final_upper, final_lower = basic_upper, basic_lower
            trend = False  # matches the reference's own bootstrap behavior - see module docstring
        else:
            final_upper = basic_upper if (basic_upper < final_upper or prev_close > final_upper) else final_upper
            final_lower = basic_lower if (basic_lower > final_lower or prev_close < final_lower) else final_lower
            prev_trend = out[i - 1][0]
            trend = (close > final_lower) if prev_trend else (close > final_upper)

        value = final_lower if trend else final_upper
        out[i] = (trend, value)

    return out


def simulate_supertrend(daily: list[dict], period: int = 10, multiplier: float = 3.0,
                         max_hold_days: int = 40, capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                         commission_per_trade: float = 20.0, max_drawdown_pct: float = 10.0,
                         enable_short: bool = True):
    series = supertrend_series(daily, period=period, multiplier=multiplier)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None  # dict: side, entry, stop, qty, days_in_trade

    for i in range(1, len(daily)):
        if halted:
            break
        trend, value = series[i]
        prev_trend, _ = series[i - 1]
        if trend is None or prev_trend is None:
            continue
        close = daily[i]["close"]

        if position is not None:
            position["days_in_trade"] += 1
            side = position["side"]
            flipped_against = (side == "long" and not trend) or (side == "short" and trend)
            timed_out = position["days_in_trade"] >= max_hold_days
            if flipped_against or timed_out:
                gross = (close - position["entry"]) * position["qty"] if side == "long" \
                    else (position["entry"] - close) * position["qty"]
                net = gross - commission_per_trade
                capital_track += net
                peak = max(peak, capital_track)
                dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if max_dd >= max_drawdown_pct:
                    halted = True
                trades.append(dict(date=daily[i]["date"], side=side,
                                    outcome="flip" if flipped_against else "timeout",
                                    gross_pnl=gross, net_pnl=net))
                position = None

        if position is None and not halted and prev_trend != trend:
            side = "long" if trend else ("short" if enable_short else None)
            if side is not None:
                stop = value
                risk_per_unit = abs(close - stop)
                if risk_per_unit > 0:
                    risk_amount = capital_track * risk_per_trade_pct / 100
                    qty = risk_amount / risk_per_unit
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
    return simulate_supertrend(before, **kwargs), simulate_supertrend(after, **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate_supertrend(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--st-period", type=int, default=10)
    parser.add_argument("--multiplier", type=float, default=3.0)
    parser.add_argument("--max-hold-days", type=int, default=40)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--no-short", action="store_true", help="literature-backed long/flat-only mode")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(period=args.st_period, multiplier=args.multiplier, max_hold_days=args.max_hold_days,
                  capital=args.capital, risk_per_trade_pct=args.risk_per_trade_pct,
                  commission_per_trade=args.commission_per_trade, enable_short=not args.no_short)
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
        result = simulate_supertrend(daily, **kwargs)
        _report("Full period", result, args.capital, years)
