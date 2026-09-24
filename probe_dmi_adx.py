"""
Probe: the Directional Movement Index / Average Directional Index system
(J. Welles Wilder Jr., "New Concepts in Technical Trading Systems", 1978 -
the same book RSI, ATR, and Parabolic SAR, all already used in this
project, come from). Genuinely different mechanic from every trend
strategy already tried: Donchian is a fixed N-day channel, SuperTrend an
ATR-multiple ratchet band, Parabolic SAR an accelerating trailing stop -
none of them separate TREND STRENGTH from TREND DIRECTION. DMI/ADX does:
+DI/-DI measure which direction is winning (derived from how much of
today's high/low move is a genuinely NEW extreme, not just today's
range), and ADX measures how strongly EITHER direction is winning,
independent of which one - a trend-strength FILTER, not itself directional.

Formula (day i, i >= 1 for the raw directional-movement/true-range terms,
using yesterday's bar for comparison - no lookahead, since day i's own
high/low close the same recurrence a breakout signal would use):
  up_move_i   = high_i - high_{i-1}
  down_move_i = low_{i-1} - low_i
  +DM_i = up_move_i   if (up_move_i   > down_move_i and up_move_i   > 0) else 0
  -DM_i = down_move_i if (down_move_i > up_move_i   and down_move_i > 0) else 0
  TR_i  = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
  +DI_i = 100 * mean(+DM, period) / mean(TR, period)
  -DI_i = 100 * mean(-DM, period) / mean(TR, period)
  DX_i  = 100 * |+DI_i - -DI_i| / (+DI_i + -DI_i)
  ADX_i = mean(DX, adx_period)
Wilder's own publication smooths +DM/-DM/TR/DX with his particular
exponential-style running technique; this probe uses this project's own
plain rolling-average convention instead (the SAME deliberate choice
already made for SuperTrend's ATR - "the mechanism is the ratchet/
DI-vs-DI structure, not the exact smoothing method" - not expected to
change the qualitative result, and kept consistent with every other
ATR-using strategy here, which are all plain-average too).

RULE (the simplest, most-published version, matching this project's own
"try the plain textbook rule first" convention already used for RSI-2/
Bollinger/IBS/Stochastic - a LEVEL check, not an explicit crossover
detector, since the "flat until entry" state machine already means the
entry condition can only fire once per flat->position transition): long
when ADX >= `adx_threshold` (default 25, Wilder's own "trending market"
line) AND +DI > -DI; exit when -DI climbs back above +DI, or ADX falls
back below the threshold (the trend has weakened), or `max_hold_days`
times out. Short side (-DI > +DI while ADX >= threshold) is this
project's own symmetric extension, not itself literature-backed - same
caveat already attached to IBS's/volume's/52-week-high's/SAR's. ATR-based
stop (`stop_atr_multiple` x ATR, the same convention RSI-2/Squeeze/
Bollinger/MACD/Stochastic use for a signal with no natural structural
stop), capped by the Twenty-third entry's own Carver-style volatility
sizing as a matter of course (not just where a review caught it missing,
as in the Parabolic SAR entry - applied from the start here).
"""
import argparse

from indicators import average_true_range


def dmi_series(daily: list[dict], period: int = 14, adx_period: int = 14):
    """One (plus_di, minus_di, adx) tuple per day - None entries before
    each window fills. Computed only from days <= i, safe to precompute
    for the whole series up front."""
    n = len(daily)
    highs = [b["high"] for b in daily]
    lows = [b["low"] for b in daily]
    closes = [b["close"] for b in daily]

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    dx = [None] * n
    plus_di_list = [None] * n
    minus_di_list = [None] * n
    for i in range(period, n):
        atr_sum = sum(tr[i - period + 1:i + 1])
        if atr_sum <= 0:
            continue
        plus_di = 100 * sum(plus_dm[i - period + 1:i + 1]) / atr_sum
        minus_di = 100 * sum(minus_dm[i - period + 1:i + 1]) / atr_sum
        plus_di_list[i] = plus_di
        minus_di_list[i] = minus_di
        denom = plus_di + minus_di
        dx[i] = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0

    out = [(None, None, None)] * n
    for i in range(n):
        if dx[i] is None:
            continue
        window = [v for v in dx[max(0, i - adx_period + 1):i + 1] if v is not None]
        if len(window) < adx_period:
            continue
        out[i] = (plus_di_list[i], minus_di_list[i], sum(window) / len(window))
    return out


def simulate_dmi(daily: list[dict], period: int = 14, adx_period: int = 14, adx_threshold: float = 25.0,
                  stop_atr_multiple: float = 2.0, atr_period: int = 14, max_hold_days: int = 40,
                  capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                  commission_per_trade: float = 20.0, max_drawdown_pct: float = 10.0,
                  enable_short: bool = True):
    series = dmi_series(daily, period=period, adx_period=adx_period)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None

    for i in range(atr_period + period + adx_period, len(daily)):
        if halted:
            break
        plus_di, minus_di, adx = series[i]
        high, low, close = daily[i]["high"], daily[i]["low"], daily[i]["close"]

        if position is not None:
            position["days_in_trade"] += 1
            side = position["side"]
            stopped = (side == "long" and low <= position["stop"]) or \
                      (side == "short" and high >= position["stop"])
            exit_price = position["stop"] if stopped else close
            reverted = (not stopped) and plus_di is not None and (
                (side == "long" and minus_di > plus_di) or (side == "short" and plus_di > minus_di)
                or adx < adx_threshold)
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
        elif not halted and plus_di is not None and adx >= adx_threshold:
            atr = average_true_range(daily[i - atr_period:i + 1], period=atr_period)
            if atr is None or atr <= 0:
                continue
            side = None
            if plus_di > minus_di:
                side = "long"
                stop = close - stop_atr_multiple * atr
            elif enable_short and minus_di > plus_di:
                side = "short"
                stop = close + stop_atr_multiple * atr
            if side is not None:
                risk_per_unit = abs(close - stop)
                if risk_per_unit > 0:
                    risk_amount = capital_track * risk_per_trade_pct / 100
                    qty = min(risk_amount / risk_per_unit, risk_amount / atr)
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
    return simulate_dmi(before, **kwargs), simulate_dmi(after, **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate_dmi(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--dmi-period", type=int, default=14)
    parser.add_argument("--adx-period", type=int, default=14)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--stop-atr-multiple", type=float, default=2.0)
    parser.add_argument("--max-hold-days", type=int, default=40)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--no-short", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(period=args.dmi_period, adx_period=args.adx_period, adx_threshold=args.adx_threshold,
                  stop_atr_multiple=args.stop_atr_multiple, max_hold_days=args.max_hold_days,
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
        result = simulate_dmi(daily, **kwargs)
        _report("Full period", result, args.capital, years)
