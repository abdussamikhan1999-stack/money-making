"""
Probe: Parabolic SAR ("stop and reverse", J. Welles Wilder Jr., "New
Concepts in Technical Trading Systems", 1978 - the same book RSI and ATR,
both already used throughout this project, come from). Genuinely
different trend-following construction from Donchian (a FIXED N-day
channel) and SuperTrend (an ATR-multiple band that ratchets but never
accelerates): SAR's acceleration factor (AF) grows every time price makes
a new extreme in the trend's direction, so the stop tightens progressively
as a trend matures - the classic "the parabola catches up to price" shape
that gives the indicator its name. It is also the first ALWAYS-IN-MARKET
strategy tried anywhere in this project: every other strategy here has
flat periods between signals; SAR is long or short every single day once
its bootstrap window fills, since a stop-and-reverse system has no flat
state by construction.

Algorithm (Wilder's own, the version every major charting platform still
implements identically):
  SAR_i = SAR_{i-1} + AF_{i-1} * (EP_{i-1} - SAR_{i-1})
  uptrend:   SAR_i is capped at min(SAR_i, low_{i-1}, low_{i-2}) (never set
             above the last two bars' lows - Wilder's own no-penetration
             rule); reverses to downtrend if low_i < SAR_i.
  downtrend: SAR_i is floored at max(SAR_i, high_{i-1}, high_{i-2});
             reverses to uptrend if high_i > SAR_i.
  On a REVERSAL: new SAR = the extreme point (EP) just abandoned, AF
  resets to `start_af` (0.02), new EP = today's own high/low.
  Otherwise: EP updates to today's high/low if it's a new extreme in the
  trend's own direction, AND ONLY THEN does AF step up by `step_af` (0.02),
  capped at `max_af` (0.20) - the classic default triple (0.02/0.02/0.20).

Bootstrap (disclosed, not hidden): unlike a channel/band indicator, SAR
has no natural "day 0" state - it needs an initial trend, SAR level, EP
and AF before the recurrence above can run. This probe bootstraps trend
from a simple, genuinely close-to-50/50 test (uptrend iff close_1 >=
close_0 - NOT the "which side is price already on relative to a wide
band" tautology SuperTrend's own bootstrap heuristic turned out to be,
per this project's SuperTrend entry, since a one-day close comparison has
no such structural bias), initial SAR = low_0 (uptrend) / high_0
(downtrend), initial EP = high_1 / low_1, initial AF = start_af. A
synthetic monotonic-trend sanity check (not a pytest file - this
project's standalone probes don't get one, per established convention)
confirmed the recurrence locks onto the correct trend and the AF
correctly steps up on new extremes and resets on reversal, independent of
which way the one-day bootstrap guessed, before any real backtest number
was trusted.

ALWAYS IN MARKET, so there is no separate entry/exit rule to write - the
position simply flips to the opposite side the moment SAR reverses,
exactly mirroring SuperTrend's own "the flip itself IS the exit signal"
convention, except SuperTrend still has a `max_hold_days` safety cap
between flips (kept here too, for consistency, even though a real
stop-and-reverse system is not expected to sit still for 40 days without
SAR itself catching up - it can, in a narrow-range market). Entry stop for
POSITION SIZING purposes is the active SAR level at entry time itself,
the same structural-stop role Donchian's/SuperTrend's own active band
plays.
"""
import argparse

from indicators import average_true_range


def parabolic_sar_series(daily: list[dict], start_af: float = 0.02, step_af: float = 0.02, max_af: float = 0.20):
    """One (trend, sar_value) pair per day - (None, None) for the single
    bootstrap day (day 0) which has no recurrence output yet. trend is
    True (uptrend) / False (downtrend). Computed once, up front, for the
    whole series - day i's SAR only ever depends on days <= i, so this is
    safe to precompute without any lookahead risk."""
    n = len(daily)
    out = [(None, None)] * n
    if n < 2:
        return out

    highs = [b["high"] for b in daily]
    lows = [b["low"] for b in daily]
    closes = [b["close"] for b in daily]

    trend = closes[1] >= closes[0]  # disclosed bootstrap guess, see module docstring
    sar = lows[0] if trend else highs[0]
    ep = highs[1] if trend else lows[1]
    af = start_af
    out[1] = (trend, sar)

    for i in range(2, n):
        new_sar = sar + af * (ep - sar)
        if trend:
            new_sar = min(new_sar, lows[i - 1], lows[i - 2])
            if lows[i] < new_sar:
                trend, new_sar, ep, af = False, ep, lows[i], start_af
            else:
                if highs[i] > ep:
                    ep, af = highs[i], min(af + step_af, max_af)
        else:
            new_sar = max(new_sar, highs[i - 1], highs[i - 2])
            if highs[i] > new_sar:
                trend, new_sar, ep, af = True, ep, highs[i], start_af
            else:
                if lows[i] < ep:
                    ep, af = lows[i], min(af + step_af, max_af)
        sar = new_sar
        out[i] = (trend, sar)

    return out


def simulate_sar(daily: list[dict], start_af: float = 0.02, step_af: float = 0.02, max_af: float = 0.20,
                  max_hold_days: int = 40, capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                  commission_per_trade: float = 20.0, max_drawdown_pct: float = 10.0):
    series = parabolic_sar_series(daily, start_af=start_af, step_af=step_af, max_af=max_af)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None  # dict: side, entry, stop, qty, days_in_trade - always populated once bootstrapped, since SAR is always in market

    for i in range(2, len(daily)):
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
            flipped = (side == "long" and not trend) or (side == "short" and trend)
            timed_out = position["days_in_trade"] >= max_hold_days
            if flipped or timed_out:
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
                                    outcome="flip" if flipped else "timeout",
                                    gross_pnl=gross, net_pnl=net))
                position = None

        if position is None and not halted:
            # a genuine SAR flip re-enters immediately (always in market); a timeout re-enters
            # on the SAME side the very next bar too, since the trend hasn't actually reversed
            side = "long" if trend else "short"
            stop = value
            risk_per_unit = abs(close - stop)
            if risk_per_unit > 0:
                risk_amount = capital_track * risk_per_trade_pct / 100
                qty = risk_amount / risk_per_unit
                # SAR is DESIGNED to converge on price as AF accelerates (that's the whole
                # "parabola catches up" mechanic) - unlike an ATR-scaled stop (RSI-2/Squeeze/
                # Bollinger/MACD/SuperTrend all use one), risk_per_unit here can legitimately
                # shrink toward zero in a mature trend, which would blow qty up unboundedly.
                # Cap with the Carver-style ATR sizing this project already built and validated
                # for exactly this situation (Twenty-third entry) - review finding, fixed before
                # any number below was trusted.
                atr = average_true_range(daily[max(0, i - 14):i + 1], period=14)
                if atr and atr > 0:
                    qty = min(qty, risk_amount / atr)
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
    return simulate_sar(before, **kwargs), simulate_sar(after, **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate_sar(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--start-af", type=float, default=0.02)
    parser.add_argument("--step-af", type=float, default=0.02)
    parser.add_argument("--max-af", type=float, default=0.20)
    parser.add_argument("--max-hold-days", type=int, default=40)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(start_af=args.start_af, step_af=args.step_af, max_af=args.max_af,
                  max_hold_days=args.max_hold_days, capital=args.capital,
                  risk_per_trade_pct=args.risk_per_trade_pct, commission_per_trade=args.commission_per_trade)
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
        result = simulate_sar(daily, **kwargs)
        _report("Full period", result, args.capital, years)
