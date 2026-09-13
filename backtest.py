"""
Backtest the Highest-Open/Lowest-Open strategy against historical data —
either free Yahoo Finance data (no Kite subscription needed) or real Kite
data (needs a paid Kite Connect "Connect"-tier subscription).

Usage:
    # Free, no Kite subscription needed:
    python backtest.py --source yfinance --symbol RELIANCE.NS --period 30d
    python backtest.py --source yfinance --symbol ^NSEI --period 60d --m15-filter

    # Real Kite data (needs Connect-tier, not the free Personal tier):
    python backtest.py --source kite --token 256265 --from 2026-08-01 --to 2026-09-01

    # Walk-forward overfitting check (Davey): run the SAME rule on two
    # independent halves of the sample and compare, instead of trusting one
    # whole-period number:
    python backtest.py --source yfinance --symbol INFY.NS --period 60d --walk-forward

CAVEATS:
  - This is an OHLC-order approximation, not a true tick backtest: within
    each intrabar candle, prices are fed to the strategy in the order
    open -> high -> low -> close. The real intra-candle path is unknown from
    OHLC alone, so this can misorder which of a candle's high/low came
    first. Reasonable approximation, not a guarantee.
  - yfinance's intraday history is capped by Yahoo (60m ~730 days back,
    15m/5m ~60 days) and its NSE data is best-effort, not exchange-of-record
    — fine to validate strategy logic, not a substitute for real broker data
    before trading real money.
"""
import argparse
from datetime import datetime

from strategy import HighLowOpenStrategy, HighLowOpenBreakoutStrategy
from paper_broker import PaperBroker
from risk import RiskManager

VARIANTS = {
    "reversal": lambda m15_filter: HighLowOpenStrategy(m15_filter=m15_filter),
    "breakout": lambda m15_filter: HighLowOpenBreakoutStrategy(),
}


def simulate(h1: list[dict], m15: list[dict], intraday: list[dict],
             capital: float = 100_000.0, m15_filter: bool = False, variant: str = "reversal",
             max_drawdown_pct: float = 10.0, commission_per_trade: float = 0.0,
             breakeven_trigger: float = 5.0, breakeven_offset: float = 1.0,
             trail_trigger: float = 10.0, trail_offset: float = 5.0) -> tuple[PaperBroker, RiskManager]:
    """Core, source-agnostic backtest loop. `intraday` is the finest-grained
    series available (Kite 'minute' candles, or yfinance 5m/15m bars) used
    as the intrabar price-path proxy. Returns the broker (trades/P&L) and the
    RiskManager (so callers can see drawdown/halt state, not just P&L).

    `variant`: "reversal" (the source thread's rule — fade the breakout) or
    "breakout" (its trend-following inverse — trade WITH the breakout)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}, expected one of {list(VARIANTS)}")

    m15_open_at = {c["date"]: c["open"] for c in m15}

    def m15_open_for(ts: datetime) -> float | None:
        bucket = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        return m15_open_at.get(bucket)

    intraday_by_hour: dict = {}
    for c in intraday:
        hour_bucket = c["date"].replace(minute=0, second=0, microsecond=0)
        intraday_by_hour.setdefault(hour_bucket, []).append(c)

    broker = PaperBroker(commission_per_trade=commission_per_trade)
    risk = RiskManager(capital=capital, max_drawdown_pct=max_drawdown_pct)
    strat = VARIANTS[variant](m15_filter)
    current_day = None

    for bar in h1:
        day = bar["date"].date()
        if day != current_day:
            strat.reset_session()
            risk.reset_day()
            current_day = day
        strat.on_bar_open(bar["open"])

        hour_bucket = bar["date"].replace(minute=0, second=0, microsecond=0)
        for sub in intraday_by_hour.get(hour_bucket, []):
            for px in (sub["open"], sub["high"], sub["low"], sub["close"]):
                if broker.in_position:
                    pnl = broker.on_price(px)
                    if pnl is not None:
                        risk.record_trade(pnl)
                    continue
                if risk.trading_halted():
                    continue
                signal = strat.on_price(px, m15_open_for(sub["date"]))
                if signal:
                    qty = risk.position_size(signal.entry_price, signal.stop_loss)
                    if qty > 0:
                        broker.enter(signal.side, signal.entry_price, qty, signal.stop_loss,
                                     breakeven_trigger, breakeven_offset, trail_trigger, trail_offset)

    return broker, risk


def split_by_date(candles: list[dict], cutoff_date) -> tuple[list[dict], list[dict]]:
    """cutoff_date is a plain date (not datetime) — compared against each
    candle's own date() so this works regardless of whether the source's
    timestamps are timezone-naive (Kite) or timezone-aware (yfinance)."""
    before = [c for c in candles if c["date"].date() < cutoff_date]
    after = [c for c in candles if c["date"].date() >= cutoff_date]
    return before, after


def walk_forward(h1: list[dict], m15: list[dict], intraday: list[dict],
                  capital: float = 100_000.0, m15_filter: bool = False, variant: str = "reversal",
                  split_ratio: float = 0.5,
                  **trail_kwargs) -> tuple[tuple[PaperBroker, RiskManager], tuple[PaperBroker, RiskManager]]:
    """Davey-style overfitting check: run the IDENTICAL rule on two
    independent halves of the sample (each starting with its own fresh
    capital/risk state) and let the caller compare them. A real edge should
    look broadly similar on both; profitable on one half and badly losing on
    the other is the classic signature of noise, not a robust edge — exactly
    what happened when toggling the M15 filter flipped several of the
    9-instrument comparison's results. This is a cheap sanity check, not a
    substitute for real walk-forward parameter optimization."""
    if not h1:
        raise ValueError("no data to split")
    dates = sorted({c["date"].date() for c in h1})
    cutoff_date = dates[max(1, int(len(dates) * split_ratio))]

    h1_a, h1_b = split_by_date(h1, cutoff_date)
    m15_a, m15_b = split_by_date(m15, cutoff_date)
    intraday_a, intraday_b = split_by_date(intraday, cutoff_date)

    in_sample = simulate(h1_a, m15_a, intraday_a, capital, m15_filter, variant, **trail_kwargs)
    out_of_sample = simulate(h1_b, m15_b, intraday_b, capital, m15_filter, variant, **trail_kwargs)
    return in_sample, out_of_sample


def _fetch_yfinance(symbol: str, period: str) -> tuple[list[dict], list[dict], list[dict]]:
    from data_yfinance import fetch_candles
    return (fetch_candles(symbol, "60m", period),
            fetch_candles(symbol, "15m", period),
            fetch_candles(symbol, "5m", period))


def _fetch_kite(instrument_token: int, from_date: datetime, to_date: datetime) -> tuple[list[dict], list[dict], list[dict]]:
    from kite_client import get_kite_client
    from data import fetch_candles
    kite = get_kite_client()
    return (fetch_candles(kite, instrument_token, "60minute", from_date, to_date),
            fetch_candles(kite, instrument_token, "15minute", from_date, to_date),
            fetch_candles(kite, instrument_token, "minute", from_date, to_date))


def run_backtest_yfinance(symbol: str, period: str = "30d", capital: float = 100_000.0,
                           m15_filter: bool = False, variant: str = "reversal", **trail_kwargs):
    return simulate(*_fetch_yfinance(symbol, period), capital, m15_filter, variant, **trail_kwargs)


def run_backtest_kite(instrument_token: int, from_date: datetime, to_date: datetime, capital: float = 100_000.0,
                       m15_filter: bool = False, variant: str = "reversal", **trail_kwargs):
    return simulate(*_fetch_kite(instrument_token, from_date, to_date), capital, m15_filter, variant, **trail_kwargs)


def walk_forward_yfinance(symbol: str, period: str = "60d", capital: float = 100_000.0, m15_filter: bool = False,
                           variant: str = "reversal", split_ratio: float = 0.5, **trail_kwargs):
    return walk_forward(*_fetch_yfinance(symbol, period), capital, m15_filter, variant, split_ratio, **trail_kwargs)


def walk_forward_kite(instrument_token: int, from_date: datetime, to_date: datetime, capital: float = 100_000.0,
                       m15_filter: bool = False, variant: str = "reversal", split_ratio: float = 0.5, **trail_kwargs):
    return walk_forward(*_fetch_kite(instrument_token, from_date, to_date), capital, m15_filter, variant,
                         split_ratio, **trail_kwargs)


def _report(label: str, broker: PaperBroker, risk: RiskManager) -> None:
    halted = " [DRAWDOWN HALTED]" if risk.drawdown_halted else ""
    print(f"{label}: Trades {len(broker.trade_log)}  P&L {broker.cash_pnl:.2f}  "
          f"Max drawdown {risk.drawdown_from_peak_pct:.1f}%{halted}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["yfinance", "kite"], default="yfinance")
    parser.add_argument("--variant", choices=["reversal", "breakout"], default="reversal",
                         help="reversal = source thread's rule (fade the breakout); "
                              "breakout = trend-following inverse (trade WITH the breakout)")
    parser.add_argument("--symbol", help="yfinance ticker, e.g. RELIANCE.NS or ^NSEI (source=yfinance)")
    parser.add_argument("--period", default="30d", help="yfinance lookback, e.g. 30d, 60d (source=yfinance)")
    parser.add_argument("--token", type=int, help="Kite instrument_token (source=kite)")
    parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD (source=kite)")
    parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD (source=kite)")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--m15-filter", action="store_true")
    parser.add_argument("--walk-forward", action="store_true",
                         help="split the sample in half and report both halves separately (overfitting check)")
    parser.add_argument("--breakeven-trigger", type=float, default=5.0,
                         help="profit (price units) to move stop to breakeven+offset")
    parser.add_argument("--breakeven-offset", type=float, default=1.0)
    parser.add_argument("--trail-trigger", type=float, default=10.0,
                         help="profit (price units) to trail stop to entry+trail-offset")
    parser.add_argument("--trail-offset", type=float, default=5.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0,
                         help="cumulative drawdown %% of capital that permanently halts trading")
    parser.add_argument("--commission-per-trade", type=float, default=0.0,
                         help="flat cost deducted per round-trip trade (commission + estimated slippage) — "
                              "0 by default, but a high-trade-count result should ALWAYS be re-checked with "
                              "a realistic value before trusting it")
    args = parser.parse_args()

    extra_kwargs = dict(
        max_drawdown_pct=args.max_drawdown_pct, commission_per_trade=args.commission_per_trade,
        breakeven_trigger=args.breakeven_trigger, breakeven_offset=args.breakeven_offset,
        trail_trigger=args.trail_trigger, trail_offset=args.trail_offset,
    )

    if args.source == "yfinance":
        if not args.symbol:
            parser.error("--symbol is required for --source yfinance")
        if args.walk_forward:
            (b1, r1), (b2, r2) = walk_forward_yfinance(args.symbol, args.period, args.capital,
                                                        args.m15_filter, args.variant, **extra_kwargs)
        else:
            broker, risk = run_backtest_yfinance(args.symbol, args.period, args.capital,
                                                  args.m15_filter, args.variant, **extra_kwargs)
    else:
        if not (args.token and args.from_date and args.to_date):
            parser.error("--token, --from, and --to are required for --source kite")
        from_dt = datetime.strptime(args.from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(args.to_date, "%Y-%m-%d")
        if args.walk_forward:
            (b1, r1), (b2, r2) = walk_forward_kite(args.token, from_dt, to_dt, args.capital,
                                                    args.m15_filter, args.variant, **extra_kwargs)
        else:
            broker, risk = run_backtest_kite(args.token, from_dt, to_dt, args.capital,
                                              args.m15_filter, args.variant, **extra_kwargs)

    if args.walk_forward:
        _report("In-sample  (first half)", b1, r1)
        _report("Out-of-sample (2nd half)", b2, r2)
        consistent = (b1.cash_pnl > 0) == (b2.cash_pnl > 0)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves"
              f" — {'plausible edge, keep validating' if consistent else 'looks like noise/overfitting, not a real edge'}")
    else:
        _report("Result", broker, risk)
        for t in broker.trade_log:
            print(t)
