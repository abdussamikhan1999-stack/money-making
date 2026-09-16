"""
Probe: Trend + Volume-Confirmed IBS Reversion — an ORIGINAL strategy for
this project, not sourced from a forum thread or open-source repo like
every mechanism before it. Motivated directly by three of this project's
own prior findings, combined as an explicit hypothesis rather than a
citation:

  1. IBS mean reversion (CLAUDE.md's "Thirteenth") found exactly one
     survivor (GC=F) out of 8 instruments — a 12.5% hit rate, at/below
     this project's chance-level floor. It trades every IBS extreme with
     no context about the prevailing trend or whether real money is
     backing the move.
  2. RSI-2 (CLAUDE.md's "Third") found its SMA trend filter ("only buy
     dips ABOVE the long-term trend") was the most theoretically coherent,
     robust result in the whole project — the edge held up specifically
     BECAUSE trades were filtered to align with the prevailing direction.
  3. Volume confirmation via CMF (CLAUDE.md's "Sixteenth") found its own
     one clean GC=F survivor — same instrument IBS's survivor was.

Hypothesis: an IBS extreme unfiltered is often a falling knife (bad) or a
low-conviction wiggle (noise); requiring it to occur WITH the trend AND
with money-flow already accumulating (CMF > 0) should filter out both
failure modes and might extend IBS's real-but-narrow gold edge to more
instruments than the 1/8 it found alone. This is a genuine three-way
ENTRY-side confirmation, not the exit-side stacking this project already
found actively HURTS a working strategy (CLAUDE.md's "Eleventh": bolting
an ATR trailing-profit overlay onto RSI-2's own exit made every instrument
worse) - a different claim, not yet tested here.

Rule:
  long:  IBS < ibs_entry_long   AND close > trend_sma(trend_period)
         AND CMF(cmf_period) > 0
  short: IBS > ibs_entry_short  AND close < trend_sma(trend_period)
         AND CMF(cmf_period) < 0   (this project's own symmetric extension,
         same caveat as every other strategy's short side here - the
         literature is long-only)
  exit:  IBS crosses back through ibs_exit (long) / 1-ibs_exit (short),
         OR the trend filter flips against the position (borrowed from
         RSI-2's own exit rule), OR max_hold_days, OR the ATR stop is hit
         intrabar (OHLC-order approximation, same as every backtest here).

Not ported into daily_strategy.py's push/check_entry(close)/check_exit
interface: IBS needs TODAY's own high/low at the entry decision, the same
shape-mismatch reasoning already documented for probe_ibs.py itself and
the momentum-rotation/pairs-trading/gap-fill probes - own loop instead of
distorting the shared interface.
"""
import argparse

from indicators import internal_bar_strength as ibs_of, sma, chaikin_money_flow


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


def simulate(daily: list[dict], trend_period: int = 50, cmf_period: int = 20,
             ibs_entry_long: float = 0.2, ibs_entry_short: float = 0.8, ibs_exit: float = 0.5,
             stop_atr_multiple: float = 3.0, atr_period: int = 14, max_hold_days: int = 10,
             capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
             commission_per_trade: float = 20.0, max_drawdown_pct: float = 10.0,
             enable_short: bool = True):
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None  # dict: side, entry, stop, days_in_trade, qty

    for i, bar in enumerate(daily):
        if halted:
            break

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
                ibs = ibs_of(bar)
                trend = sma([d["close"] for d in daily[max(0, i - trend_period + 1):i + 1]])
                timed_out = position["days_in_trade"] >= max_hold_days
                trend_flipped = trend is not None and (
                    (side == "long" and bar["close"] < trend) or (side == "short" and bar["close"] > trend))
                ibs_exit_hit = ibs is not None and (
                    (side == "long" and ibs > ibs_exit) or (side == "short" and ibs < (1 - ibs_exit)))
                if ibs_exit_hit or trend_flipped:
                    exit_price, outcome = bar["close"], "ibs_exit" if ibs_exit_hit else "trend_flip"
                elif timed_out:
                    exit_price, outcome = bar["close"], "timeout"

            if exit_price is not None:
                gross = (exit_price - position["entry"]) * position["qty"] if side == "long" \
                    else (position["entry"] - exit_price) * position["qty"]
                net = gross - commission_per_trade
                capital_track += net
                peak = max(peak, capital_track)
                dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if max_dd >= max_drawdown_pct:
                    halted = True
                trades.append(dict(date=bar["date"], side=side, outcome=outcome,
                                    gross_pnl=gross, net_pnl=net))
                position = None

        elif not halted and i >= trend_period:
            ibs = ibs_of(bar)
            atr = _atr(daily, i, atr_period)
            trend = sma([d["close"] for d in daily[i - trend_period + 1:i + 1]])
            cmf = chaikin_money_flow(daily[max(0, i - cmf_period + 1):i + 1], period=cmf_period)
            if ibs is not None and atr and atr > 0 and trend is not None and cmf is not None:
                side = None
                if ibs < ibs_entry_long and bar["close"] > trend and cmf > 0:
                    side = "long"
                elif enable_short and ibs > ibs_entry_short and bar["close"] < trend and cmf < 0:
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
    return simulate(daily[:cutoff], **kwargs), simulate(daily[cutoff:], **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--trend-period", type=int, default=50)
    parser.add_argument("--cmf-period", type=int, default=20)
    parser.add_argument("--ibs-entry-long", type=float, default=0.2)
    parser.add_argument("--ibs-entry-short", type=float, default=0.8)
    parser.add_argument("--ibs-exit", type=float, default=0.5)
    parser.add_argument("--stop-atr-multiple", type=float, default=3.0)
    parser.add_argument("--max-hold-days", type=int, default=10)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--no-short", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(trend_period=args.trend_period, cmf_period=args.cmf_period,
                  ibs_entry_long=args.ibs_entry_long, ibs_entry_short=args.ibs_entry_short,
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
        result = simulate(daily, **kwargs)
        _report("Full period", result, args.capital, years)
