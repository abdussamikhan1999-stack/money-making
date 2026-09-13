"""
Backtest daily-bar trend-following strategies (see daily_strategy.py)
against yfinance daily data. No 60-day intraday cap here - daily bars go
back years, so this can be walk-forward validated across multiple market
regimes instead of one 60-day window.

Usage:
    python backtest_daily.py --symbol RELIANCE.NS --period 10y
    python backtest_daily.py --symbol '^NSEI' --period 10y --walk-forward
    python backtest_daily.py --symbol CL=F --period 10y --entry-period 55 --commission-per-trade 20
"""
import argparse

from paper_broker import PaperBroker
from risk import RiskManager
from daily_strategy import DonchianBreakoutStrategy, Side
from backtest import split_by_date, _report  # reuse: same date-splitting + reporting used for intraday backtests


def simulate_daily(daily: list[dict], capital: float = 100_000.0, entry_period: int = 20, exit_period: int = 0,
                    max_drawdown_pct: float = 10.0, commission_per_trade: float = 0.0) -> tuple[PaperBroker, RiskManager]:
    """breakeven_trigger/trail_trigger are passed to PaperBroker.enter() as
    +inf, deliberately disabling strategy.py's TrailingStopManager (its
    fixed point thresholds are tuned for forex pips - see daily_strategy.py's
    docstring for the fake-100%-win-rate bug this caused before it was
    caught). The position is held at its ORIGINAL structural stop until
    either that stop is hit or DonchianBreakoutStrategy.check_exit() fires
    on the shorter exit channel - real trend-following exit logic, not a
    tight trail that caps profit near zero."""
    broker = PaperBroker(commission_per_trade=commission_per_trade)
    risk = RiskManager(capital=capital, max_drawdown_pct=max_drawdown_pct)
    strat = DonchianBreakoutStrategy(entry_period=entry_period, exit_period=exit_period)
    current_side: Side | None = None

    for bar in daily:
        if broker.in_position:
            # OHLC-order approximation of the day's path (open->high->low->close) -
            # same approach as backtest.py's intraday loop, so an intraday stop
            # breach isn't missed just because the close recovered above it.
            for px in (bar["open"], bar["high"], bar["low"], bar["close"]):
                if not broker.in_position:
                    break
                pnl = broker.on_price(px)
                if pnl is not None:
                    risk.record_trade(pnl)
                    current_side = None
            if broker.in_position and strat.check_exit(bar["close"], current_side):
                pnl = broker.close(bar["close"])
                risk.record_trade(pnl)
                current_side = None
        elif not risk.trading_halted():
            signal = strat.check_entry(bar["close"])
            if signal:
                qty = risk.position_size(signal.entry_price, signal.stop_loss)
                if qty > 0:
                    broker.enter(signal.side, signal.entry_price, qty, signal.stop_loss,
                                 breakeven_trigger=float("inf"), trail_trigger=float("inf"))
                    current_side = signal.side
        strat.push(bar["high"], bar["low"])

    return broker, risk


def walk_forward_daily(daily: list[dict], capital: float = 100_000.0, entry_period: int = 20,
                        exit_period: int = 0, split_ratio: float = 0.5, **kwargs):
    if not daily:
        raise ValueError("no data to split")
    dates = sorted({c["date"].date() for c in daily})
    cutoff_date = dates[max(1, int(len(dates) * split_ratio))]
    before, after = split_by_date(daily, cutoff_date)
    in_sample = simulate_daily(before, capital, entry_period, exit_period, **kwargs)
    out_of_sample = simulate_daily(after, capital, entry_period, exit_period, **kwargs)
    return in_sample, out_of_sample


def fetch_daily_yfinance(symbol: str, period: str) -> list[dict]:
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="yfinance ticker, e.g. RELIANCE.NS, ^NSEI, CL=F")
    parser.add_argument("--period", default="5y", help="yfinance lookback, e.g. 5y, 10y, max")
    parser.add_argument("--entry-period", type=int, default=20, help="Donchian entry channel lookback in days")
    parser.add_argument("--exit-period", type=int, default=0,
                         help="Donchian exit channel lookback in days (0 = entry_period // 2, classic Turtle ratio)")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--commission-per-trade", type=float, default=0.0)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(max_drawdown_pct=args.max_drawdown_pct, commission_per_trade=args.commission_per_trade)

    if args.walk_forward:
        (b1, r1), (b2, r2) = walk_forward_daily(daily, args.capital, args.entry_period, args.exit_period, **kwargs)
        _report("In-sample  (first half)", b1, r1)
        _report("Out-of-sample (2nd half)", b2, r2)
        consistent = (b1.cash_pnl > 0) == (b2.cash_pnl > 0)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves"
              f" - {'plausible edge, keep validating' if consistent else 'looks like noise/overfitting, not a real edge'}")
        if r1.drawdown_halted or r2.drawdown_halted:
            print("NOTE: at least one half hit the drawdown breaker - a same-sign match can be a hollow "
                  "artifact of both halves hitting the floor rather than real agreement (see CLAUDE.md).")
    else:
        broker, risk = simulate_daily(daily, args.capital, args.entry_period, args.exit_period, **kwargs)
        _report("Result", broker, risk)
        for t in broker.trade_log:
            print(t)
