"""
Backtest daily-bar strategies (see daily_strategy.py) against yfinance daily
data. No 60-day intraday cap here - daily bars go back years, so this can
be walk-forward validated across multiple market regimes instead of one
60-day window.

Usage:
    python backtest_daily.py --strategy donchian --symbol RELIANCE.NS --period 10y
    python backtest_daily.py --strategy rsi2 --symbol '^NSEI' --period 10y --walk-forward
    python backtest_daily.py --strategy donchian --symbol CL=F --period 10y --entry-period 55 --commission-per-trade 20
"""
import argparse

from paper_broker import PaperBroker
from risk import RiskManager
from daily_strategy import (
    DonchianBreakoutStrategy, ConnorsRSI2Strategy, ThreeBarBreakoutStrategy, SqueezeMomentumStrategy,
    VolumeConfirmationStrategy, TurtleSoupStrategy, MACDStrategy, Side,
)
from backtest import split_by_date, _report  # reuse: same date-splitting + reporting used for intraday backtests

STRATEGIES = {
    "donchian": lambda args: DonchianBreakoutStrategy(entry_period=args.entry_period, exit_period=args.exit_period),
    "rsi2": lambda args: ConnorsRSI2Strategy(
        rsi_period=args.rsi_period, trend_period=args.trend_period, exit_sma_period=args.exit_sma_period,
        rsi_entry_long=args.rsi_entry_long, rsi_entry_short=args.rsi_entry_short,
        stop_atr_multiple=args.stop_atr_multiple,
    ),
    "threebar": lambda args: ThreeBarBreakoutStrategy(
        compression_atr_mult=args.compression_atr_mult, stop_buffer_atr_mult=args.stop_buffer_atr_mult,
        target_r_multiple=args.target_r_multiple, max_hold_days=args.max_hold_days,
    ),
    "squeeze": lambda args: SqueezeMomentumStrategy(
        length=args.squeeze_length, bb_mult=args.squeeze_bb_mult, kc_mult=args.squeeze_kc_mult,
        stop_atr_multiple=args.squeeze_stop_atr_multiple, max_hold_days=args.squeeze_max_hold_days,
    ),
    "volume": lambda args: VolumeConfirmationStrategy(
        cmf_period=args.cmf_period, obv_period=args.obv_period,
        stop_atr_multiple=args.volume_stop_atr_multiple, max_hold_days=args.volume_max_hold_days,
    ),
    "turtlesoup": lambda args: TurtleSoupStrategy(
        channel_period=args.ts_channel_period, stop_buffer_atr_mult=args.ts_stop_buffer_atr_mult,
        target_r_multiple=args.ts_target_r_multiple, max_hold_days=args.ts_max_hold_days,
    ),
    "macd": lambda args: MACDStrategy(
        fast_period=args.macd_fast_period, slow_period=args.macd_slow_period,
        signal_period=args.macd_signal_period, stop_atr_multiple=args.macd_stop_atr_multiple,
        max_hold_days=args.macd_max_hold_days,
    ),
}


def simulate_daily(daily: list[dict], strategy, capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                    max_drawdown_pct: float = 10.0, commission_per_trade: float = 0.0,
                    breakeven_atr_mult: float | None = None, trail_atr_mult: float | None = None,
                    breakeven_offset_atr_mult: float = 0.0, trail_offset_atr_mult: float = 0.5,
                    ) -> tuple[PaperBroker, RiskManager]:
    """`strategy` is any daily_strategy.py object implementing push(bar) /
    check_entry(close) / check_exit(close, side).

    By default breakeven_trigger/trail_trigger are passed to PaperBroker.enter()
    as +inf, deliberately disabling strategy.py's TrailingStopManager (its
    fixed point thresholds are tuned for forex pips - see daily_strategy.py's
    docstring for the fake-100%-win-rate bug this caused before it was
    caught). Positions are held at their ORIGINAL structural/ATR stop until
    either that stop is hit or the strategy's own check_exit() fires.

    Passing breakeven_atr_mult/trail_atr_mult (both None by default -> the
    old inf/inf behavior, unchanged) opts a strategy INTO profit-booking:
    the same TrailingStopManager mechanism, but scaled to THIS entry's own
    ATR (via strategy.current_atr(), when the strategy exposes it) instead
    of reusing the raw forex-pip defaults that caused the earlier bug. Only
    strategies that implement current_atr() (currently ConnorsRSI2Strategy)
    can use this; others silently keep the inf/inf no-op since there's no
    per-entry ATR to scale from."""
    broker = PaperBroker(commission_per_trade=commission_per_trade)
    risk = RiskManager(capital=capital, risk_per_trade_pct=risk_per_trade_pct, max_drawdown_pct=max_drawdown_pct)
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
            if broker.in_position and strategy.check_exit(bar["close"], current_side):
                pnl = broker.close(bar["close"])
                risk.record_trade(pnl)
                current_side = None
        elif not risk.trading_halted():
            signal = strategy.check_entry(bar["close"])
            if signal:
                qty = risk.position_size(signal.entry_price, signal.stop_loss)
                if qty > 0:
                    breakeven_trigger = trail_trigger = float("inf")
                    breakeven_offset = 1.0
                    trail_offset = 5.0
                    atr = getattr(strategy, "current_atr", lambda: None)()
                    if atr and breakeven_atr_mult is not None and trail_atr_mult is not None:
                        breakeven_trigger = breakeven_atr_mult * atr
                        trail_trigger = trail_atr_mult * atr
                        breakeven_offset = breakeven_offset_atr_mult * atr
                        trail_offset = trail_offset_atr_mult * atr
                    broker.enter(signal.side, signal.entry_price, qty, signal.stop_loss,
                                 breakeven_trigger=breakeven_trigger, breakeven_offset=breakeven_offset,
                                 trail_trigger=trail_trigger, trail_offset=trail_offset)
                    current_side = signal.side
        strategy.push(bar)
        risk.reset_day()  # one bar IS one day here; without this, RiskManager's "daily"
        # loss breaker (default 2% of capital, meant to reset every day) never resets and
        # silently becomes a permanent cumulative-loss-since-inception halt instead — a real
        # bug found and fixed after it collapsed every trade above ~2% cumulative realized
        # loss to zero further trades for the rest of a 10-year run. max_drawdown_pct (which
        # DOES persist across days by design) remains the real cross-time risk control here.

    return broker, risk


def walk_forward_daily(daily: list[dict], strategy_factory, capital: float = 100_000.0,
                        split_ratio: float = 0.5, **kwargs):
    """`strategy_factory` is a zero-arg callable returning a FRESH strategy
    instance for each half (so state from one half never leaks into the
    other)."""
    if not daily:
        raise ValueError("no data to split")
    dates = sorted({c["date"].date() for c in daily})
    cutoff_date = dates[max(1, int(len(dates) * split_ratio))]
    before, after = split_by_date(daily, cutoff_date)
    in_sample = simulate_daily(before, strategy_factory(), capital, **kwargs)
    out_of_sample = simulate_daily(after, strategy_factory(), capital, **kwargs)
    return in_sample, out_of_sample


def fetch_daily_yfinance(symbol: str, period: str) -> list[dict]:
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="donchian")
    parser.add_argument("--symbol", required=True, help="yfinance ticker, e.g. RELIANCE.NS, ^NSEI, CL=F")
    parser.add_argument("--period", default="5y", help="yfinance lookback, e.g. 5y, 10y, max")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--commission-per-trade", type=float, default=0.0)
    parser.add_argument("--breakeven-atr-mult", type=float, default=None,
                         help="[profit-booking] lock to breakeven after this many ATRs of profit "
                              "(only takes effect on strategies exposing current_atr(), e.g. rsi2; "
                              "requires --trail-atr-mult too)")
    parser.add_argument("--trail-atr-mult", type=float, default=None,
                         help="[profit-booking] trail the stop after this many ATRs of profit")
    parser.add_argument("--walk-forward", action="store_true")
    # donchian params
    parser.add_argument("--entry-period", type=int, default=20, help="[donchian] entry channel lookback in days")
    parser.add_argument("--exit-period", type=int, default=0, help="[donchian] exit channel lookback (0 = entry//2)")
    # rsi2 params
    parser.add_argument("--rsi-period", type=int, default=2, help="[rsi2] RSI lookback")
    parser.add_argument("--trend-period", type=int, default=200, help="[rsi2] trend-filter SMA period")
    parser.add_argument("--exit-sma-period", type=int, default=5, help="[rsi2] exit SMA period")
    parser.add_argument("--rsi-entry-long", type=float, default=5.0, help="[rsi2] RSI oversold threshold")
    parser.add_argument("--rsi-entry-short", type=float, default=95.0, help="[rsi2] RSI overbought threshold")
    parser.add_argument("--stop-atr-multiple", type=float, default=3.0, help="[rsi2] initial stop = N x ATR")
    # threebar params
    parser.add_argument("--compression-atr-mult", type=float, default=0.5,
                         help="[threebar] bar1/bar2 closes must be within N ATRs of each other")
    parser.add_argument("--stop-buffer-atr-mult", type=float, default=0.5,
                         help="[threebar] stop = N ATRs beyond bar2's structural low/high")
    parser.add_argument("--target-r-multiple", type=float, default=2.5,
                         help="[threebar] fixed take-profit at N x entry risk")
    parser.add_argument("--max-hold-days", type=int, default=20, help="[threebar] time-stop if neither hit")
    # squeeze params
    parser.add_argument("--squeeze-length", type=int, default=20, help="[squeeze] shared BB/KC lookback")
    parser.add_argument("--squeeze-bb-mult", type=float, default=2.0, help="[squeeze] Bollinger Band stdev multiplier")
    parser.add_argument("--squeeze-kc-mult", type=float, default=1.5, help="[squeeze] Keltner Channel ATR multiplier")
    parser.add_argument("--squeeze-stop-atr-multiple", type=float, default=2.0, help="[squeeze] initial stop = N x ATR")
    parser.add_argument("--squeeze-max-hold-days", type=int, default=20, help="[squeeze] time-stop if momentum never flips")
    # volume params
    parser.add_argument("--cmf-period", type=int, default=20, help="[volume] Chaikin Money Flow lookback")
    parser.add_argument("--obv-period", type=int, default=20, help="[volume] windowed On-Balance Volume lookback")
    parser.add_argument("--volume-stop-atr-multiple", type=float, default=2.0, help="[volume] initial stop = N x ATR")
    parser.add_argument("--volume-max-hold-days", type=int, default=20, help="[volume] time-stop if signal never flips")

    parser.add_argument("--ts-channel-period", type=int, default=20, help="[turtlesoup] N-day extreme that must fail")
    parser.add_argument("--ts-stop-buffer-atr-mult", type=float, default=0.5, help="[turtlesoup] stop = N ATRs past the failed extreme")
    parser.add_argument("--ts-target-r-multiple", type=float, default=1.5, help="[turtlesoup] fixed take-profit at N x risk")
    parser.add_argument("--ts-max-hold-days", type=int, default=5, help="[turtlesoup] time-stop if neither hit")

    parser.add_argument("--macd-fast-period", type=int, default=12, help="[macd] fast EMA period")
    parser.add_argument("--macd-slow-period", type=int, default=26, help="[macd] slow EMA period")
    parser.add_argument("--macd-signal-period", type=int, default=9, help="[macd] signal-line EMA period")
    parser.add_argument("--macd-stop-atr-multiple", type=float, default=2.0, help="[macd] initial stop = N x ATR")
    parser.add_argument("--macd-max-hold-days", type=int, default=20, help="[macd] time-stop if crossover never flips")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    strategy_factory = lambda: STRATEGIES[args.strategy](args)
    kwargs = dict(risk_per_trade_pct=args.risk_per_trade_pct,
                  max_drawdown_pct=args.max_drawdown_pct, commission_per_trade=args.commission_per_trade,
                  breakeven_atr_mult=args.breakeven_atr_mult, trail_atr_mult=args.trail_atr_mult)

    if args.walk_forward:
        (b1, r1), (b2, r2) = walk_forward_daily(daily, strategy_factory, args.capital, **kwargs)
        _report("In-sample  (first half)", b1, r1)
        _report("Out-of-sample (2nd half)", b2, r2)
        consistent = (b1.cash_pnl > 0) == (b2.cash_pnl > 0)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves"
              f" - {'plausible edge, keep validating' if consistent else 'looks like noise/overfitting, not a real edge'}")
        if r1.drawdown_halted or r2.drawdown_halted:
            print("NOTE: at least one half hit the drawdown breaker - a same-sign match can be a hollow "
                  "artifact of both halves hitting the floor rather than real agreement (see CLAUDE.md).")
    else:
        broker, risk = simulate_daily(daily, strategy_factory(), args.capital, **kwargs)
        _report("Result", broker, risk)
        for t in broker.trade_log:
            print(t)
