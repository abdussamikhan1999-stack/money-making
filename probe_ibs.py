"""
Probe: Internal Bar Strength (IBS) mean reversion — genuinely different
mechanism from every strategy in daily_strategy.py. RSI-2 (already tested,
CLAUDE.md's "Third") is a multi-day momentum oscillator computed across
several closes; IBS is a SAME-DAY positional signal — where today's close
fell within TODAY's own high-low range, nothing else:

    IBS = (close - low) / (high - low)   # 0 = closed at the day's low, 1 = at the high

Sourced from widely-cited, decades-of-data quant literature: Alvarez Quant
Trading's "Internal Bar Strength for Mean Reversion", Jonathan Kinlay's "The
Internal Bar Strength Indicator", and QuantifiedStrategies.com's IBS
writeups. The published edge is specifically documented on broad equity
INDICES (not single stocks) and is strongest during volatile/bear regimes —
this project already has ^NSEI and ^NSEBANK as Kite-tradable index
candidates, a good fit.

Published rule (long side, the literature-backed direction): buy at close
when IBS < ibs_entry_long (classic ~0.2, "closed near the day's low"), exit
when IBS closes back above ibs_exit (classic ~0.5-0.8 depending on source).
The SHORT side (IBS > ibs_entry_short -> short) is this project's own
symmetric extension for consistency with every other strategy here trading
both directions — it is NOT itself a literature-backed claim, and should be
read with extra skepticism if it looks good.

No natural structural stop exists in the published rule (same situation
RSI-2 was in), so stop_atr_multiple x ATR is used, and max_hold_days bounds
the hold the same way every other strategy in this project does (Donchian's
exit channel, RSI-2's SMA cross, ThreeBarBreakout's time-stop).

Not ported into daily_strategy.py's push/check_entry/check_exit interface:
that interface's check_entry(close) only receives TODAY's close, not
today's own high/low — fine for RSI-2 (closes only) and Donchian/ThreeBar
(prior days' high/low only), but IBS specifically needs today's OWN
high/low at the moment of the entry decision. Same shape-mismatch reasoning
already documented for the momentum-rotation, pairs-trading, and gap-fill
probes in CLAUDE.md — own loop instead of distorting the shared interface.

Uses a flat commission_per_trade (India equity delivery round-trip ~0.2%
STT+stamp is roughly proxied here as a flat rupee amount per round trip
rather than percentage — this project's daily-bar strategies use
commission_per_trade this way, e.g. `backtest_daily.py --commission-per-trade
20`), and the same OHLC-order (open->high->low->close) stop-check
approximation as every other backtest here.
"""
import argparse
import statistics


def _ibs(bar: dict) -> float | None:
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return None
    return (bar["close"] - bar["low"]) / rng


def _atr(daily: list[dict], i: int, period: int) -> float | None:
    if i < period:
        return None
    window = daily[i - period:i + 1]
    trs = [
        max(window[j]["high"] - window[j]["low"],
            abs(window[j]["high"] - window[j - 1]["close"]),
            abs(window[j]["low"] - window[j - 1]["close"]))
        for j in range(1, len(window))
    ]
    return sum(trs) / len(trs)


def simulate_ibs(daily: list[dict], ibs_entry_long: float = 0.2, ibs_entry_short: float = 0.8,
                  ibs_exit: float = 0.5, stop_atr_multiple: float = 3.0, atr_period: int = 14,
                  max_hold_days: int = 10, capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                  commission_per_trade: float = 20.0, max_drawdown_pct: float = 10.0,
                  enable_short: bool = True):
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []

    position = None  # dict: side, entry, stop, target_exit_ibs, days_in_trade, qty
    daily_loss_floor = capital * -0.02  # matches this project's 2% same-day breaker convention; reset each bar
    realized_today = 0.0

    for i, bar in enumerate(daily):
        if halted:
            break
        realized_today = 0.0  # one bar is one day here, same reset-per-bar convention as simulate_daily

        if position is not None:
            position["days_in_trade"] += 1
            side = position["side"]
            stop = position["stop"]
            exit_price = None
            outcome = None

            for px in (bar["open"], bar["high"], bar["low"], bar["close"]):
                if side == "long" and px <= stop:
                    exit_price, outcome = stop, "stop"
                    break
                if side == "short" and px >= stop:
                    exit_price, outcome = stop, "stop"
                    break

            if exit_price is None:
                ibs = _ibs(bar)
                timed_out = position["days_in_trade"] >= max_hold_days
                if side == "long" and ibs is not None and ibs > ibs_exit:
                    exit_price, outcome = bar["close"], "ibs_exit"
                elif side == "short" and ibs is not None and ibs < (1 - ibs_exit):
                    exit_price, outcome = bar["close"], "ibs_exit"
                elif timed_out:
                    exit_price, outcome = bar["close"], "timeout"

            if exit_price is not None:
                gross = (exit_price - position["entry"]) * position["qty"] if side == "long" \
                    else (position["entry"] - exit_price) * position["qty"]
                net = gross - commission_per_trade
                capital_track += net
                realized_today += net
                peak = max(peak, capital_track)
                dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if max_dd >= max_drawdown_pct:
                    halted = True
                trades.append(dict(date=bar["date"], side=side, outcome=outcome,
                                    gross_pnl=gross, net_pnl=net))
                position = None

        elif not halted:
            ibs = _ibs(bar)
            atr = _atr(daily, i, atr_period)
            if ibs is not None and atr and atr > 0:
                side = None
                if ibs < ibs_entry_long:
                    side = "long"
                elif enable_short and ibs > ibs_entry_short:
                    side = "short"
                if side is not None:
                    entry = bar["close"]
                    stop = entry - stop_atr_multiple * atr if side == "long" else entry + stop_atr_multiple * atr
                    risk_per_unit = abs(entry - stop)
                    if risk_per_unit > 0:
                        risk_amount = capital_track * risk_per_trade_pct / 100
                        qty = risk_amount / risk_per_unit
                        if qty > 0:
                            position = dict(side=side, entry=entry, stop=stop, qty=qty, days_in_trade=0)

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
    return simulate_ibs(before, **kwargs), simulate_ibs(after, **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate_ibs(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--ibs-entry-long", type=float, default=0.2)
    parser.add_argument("--ibs-entry-short", type=float, default=0.8)
    parser.add_argument("--ibs-exit", type=float, default=0.5)
    parser.add_argument("--stop-atr-multiple", type=float, default=3.0)
    parser.add_argument("--max-hold-days", type=int, default=10)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--no-short", action="store_true", help="literature-backed long-only mode")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(ibs_entry_long=args.ibs_entry_long, ibs_entry_short=args.ibs_entry_short,
                  ibs_exit=args.ibs_exit, stop_atr_multiple=args.stop_atr_multiple,
                  max_hold_days=args.max_hold_days, capital=args.capital,
                  risk_per_trade_pct=args.risk_per_trade_pct,
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
        result = simulate_ibs(daily, **kwargs)
        _report("Full period", result, args.capital, years)
