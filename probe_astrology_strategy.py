"""
Probe: astrology used AS a strategy, not just a return classifier. The
Ninety-fourth/Ninety-sixth entries only measured whether mean returns
DIFFER on astrologically-labelled days — a descriptive test, not a
tradeable rule. This entry takes folklore's own stated advice literally
and turns it into an actual entry/exit state machine, run through this
project's standard screening bar (walk-forward, both halves net-positive,
no drawdown-halt) on the same 12-instrument set used for every technical
mechanism here (DMI/ADX, Parabolic SAR, Stochastic, SuperTrend): `INFY.NS`,
`TCS.NS`, `HDFCBANK.NS`, `SBIN.NS`, `CL=F`, `GC=F`, `^NSEI`, `AXISBANK.NS`,
`ITC.NS`, `^NSEBANK`, `RELIANCE.NS`, `WIPRO.NS`.

RULE (long-only — the two tested astrological signals give one favorable
condition each, not a natural symmetric short; adding a short side here
would be this project's own invention, not literature-backed, so it's
left out rather than repeating that caveat a fourth time): enter long
when flat, NOT in Mercury retrograde (Ninety-sixth entry), AND the moon
is within its "new" window (Ninety-fourth entry) — literally "the two
astrologically auspicious conditions this project has tested both hold
at once." Exit on: Mercury turning retrograde (the specific, actively-
circulated folk advice — don't hold through a retrograde window), the
moon reaching its "full" window, an ATR stop, or `max_hold_days` (default
30 - one lunar month, the natural timescale of the signal, not fit to
data) timing out. Same ATR-stop / risk-per-trade capital sizing
convention every other strategy in this project uses.

No new astronomical code: `moon_phase`/`classify` imported from
`probe_lunar_cycle.py`, `retrograde_flags` from
`probe_mercury_retrograde.py`, unchanged, both already self-checked in
their own entries.
"""
import argparse

from indicators import average_true_range
from probe_lunar_cycle import classify as moon_classify
from probe_mercury_retrograde import retrograde_flags


def astrology_signal(daily: list[dict], window_days: float = 3.0):
    """One (is_retrograde, moon_label) pair per day, aligned to `daily`."""
    dates = [b["date"].date() if hasattr(b["date"], "date") else b["date"] for b in daily]
    retro = retrograde_flags(dates)  # len(daily)-1, aligned to dates[1:]
    retro_full = [False] + list(retro)  # day 0 has no prior day; treat as not-retrograde
    moon = [moon_classify(d, window_days) for d in dates]
    return retro_full, moon


def simulate_astrology(daily: list[dict], window_days: float = 3.0, stop_atr_multiple: float = 3.0,
                        atr_period: int = 14, max_hold_days: int = 30, capital: float = 100_000.0,
                        risk_per_trade_pct: float = 0.5, commission_per_trade: float = 20.0,
                        max_drawdown_pct: float = 10.0):
    retro, moon = astrology_signal(daily, window_days)
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None

    for i in range(atr_period, len(daily)):
        if halted:
            break
        close = daily[i]["close"]
        low, high = daily[i]["low"], daily[i]["high"]

        if position is not None:
            position["days_in_trade"] += 1
            stopped = low <= position["stop"]
            exit_price = position["stop"] if stopped else close
            astro_exit = (not stopped) and (retro[i] or moon[i] == "full")
            timed_out = (not stopped) and (not astro_exit) and position["days_in_trade"] >= max_hold_days
            if stopped or astro_exit or timed_out:
                gross = (exit_price - position["entry"]) * position["qty"]
                net = gross - commission_per_trade
                capital_track += net
                peak = max(peak, capital_track)
                dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if max_dd >= max_drawdown_pct:
                    halted = True
                outcome = "stop" if stopped else ("astro_exit" if astro_exit else "timeout")
                trades.append(dict(date=daily[i]["date"], outcome=outcome, gross_pnl=gross, net_pnl=net))
                position = None
        elif not halted and not retro[i] and moon[i] == "new":
            atr = average_true_range(daily[i - atr_period:i + 1], period=atr_period)
            if atr is None or atr <= 0:
                continue
            stop = close - stop_atr_multiple * atr
            risk_per_unit = close - stop
            if risk_per_unit > 0:
                risk_amount = capital_track * risk_per_trade_pct / 100
                qty = min(risk_amount / risk_per_unit, risk_amount / atr)
                if qty > 0:
                    position = dict(entry=close, stop=stop, qty=qty, days_in_trade=0)

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    return dict(trades=trades, final_capital=capital_track, total_gross=total_gross,
                total_net=total_net, max_dd=max_dd, halted=halted, n_trades=len(trades))


def walk_forward(daily, split_ratio=0.5, **kwargs):
    n = len(daily)
    cutoff = max(1, int(n * split_ratio))
    before, after = daily[:cutoff], daily[cutoff:]
    return simulate_astrology(before, **kwargs), simulate_astrology(after, **kwargs)


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


SCREENING_SET = ["INFY.NS", "TCS.NS", "HDFCBANK.NS", "SBIN.NS", "CL=F", "GC=F",
                  "^NSEI", "AXISBANK.NS", "ITC.NS", "^NSEBANK", "RELIANCE.NS", "WIPRO.NS"]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--screen", action="store_true", help="run the standard 12-instrument set")
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--window-days", type=float, default=3.0)
    parser.add_argument("--stop-atr-multiple", type=float, default=3.0)
    parser.add_argument("--max-hold-days", type=int, default=30)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()

    kwargs = dict(window_days=args.window_days, stop_atr_multiple=args.stop_atr_multiple,
                  max_hold_days=args.max_hold_days, capital=args.capital,
                  risk_per_trade_pct=args.risk_per_trade_pct, commission_per_trade=args.commission_per_trade)

    symbols = SCREENING_SET if args.screen else [args.symbol]
    passed = []
    for symbol in symbols:
        daily = fetch_daily_yfinance(symbol, args.period)
        span_days = (daily[-1]["date"] - daily[0]["date"]).days
        years = span_days / 365.25
        print(f"\n=== {symbol} ===")
        if args.walk_forward or args.screen:
            in_s, out_s = walk_forward(daily, **kwargs)
            _report("In-sample  (first half)", in_s, args.capital, years / 2)
            _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2)
            ok = in_s["total_net"] > 0 and out_s["total_net"] > 0 and not in_s["halted"] and not out_s["halted"]
            print(f"{'PASS' if ok else 'fail'}")
            if ok:
                passed.append(symbol)
        else:
            result = simulate_astrology(daily, **kwargs)
            _report("Full period", result, args.capital, years)

    if args.screen:
        print(f"\n=== screening result: {len(passed)}/{len(symbols)} passed ===")
        print(f"  {passed}")
