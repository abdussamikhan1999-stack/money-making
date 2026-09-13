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

from strategy import HighLowOpenStrategy
from paper_broker import PaperBroker
from risk import RiskManager


def simulate(h1: list[dict], m15: list[dict], intraday: list[dict],
             capital: float = 100_000.0, m15_filter: bool = False,
             breakeven_trigger: float = 5.0, breakeven_offset: float = 1.0,
             trail_trigger: float = 10.0, trail_offset: float = 5.0) -> PaperBroker:
    """Core, source-agnostic backtest loop. `intraday` is the finest-grained
    series available (Kite 'minute' candles, or yfinance 5m/15m bars) used
    as the intrabar price-path proxy."""
    m15_open_at = {c["date"]: c["open"] for c in m15}

    def m15_open_for(ts: datetime) -> float | None:
        bucket = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        return m15_open_at.get(bucket)

    intraday_by_hour: dict = {}
    for c in intraday:
        hour_bucket = c["date"].replace(minute=0, second=0, microsecond=0)
        intraday_by_hour.setdefault(hour_bucket, []).append(c)

    broker = PaperBroker()
    risk = RiskManager(capital=capital)
    strat = HighLowOpenStrategy(m15_filter=m15_filter)
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

    return broker


def run_backtest_yfinance(symbol: str, period: str = "30d",
                           capital: float = 100_000.0, m15_filter: bool = False, **trail_kwargs) -> PaperBroker:
    from data_yfinance import fetch_candles
    h1 = fetch_candles(symbol, "60m", period)
    m15 = fetch_candles(symbol, "15m", period)
    intraday = fetch_candles(symbol, "5m", period)
    return simulate(h1, m15, intraday, capital, m15_filter, **trail_kwargs)


def run_backtest_kite(instrument_token: int, from_date: datetime, to_date: datetime,
                       capital: float = 100_000.0, m15_filter: bool = False, **trail_kwargs) -> PaperBroker:
    from kite_client import get_kite_client
    from data import fetch_candles
    kite = get_kite_client()
    h1 = fetch_candles(kite, instrument_token, "60minute", from_date, to_date)
    m15 = fetch_candles(kite, instrument_token, "15minute", from_date, to_date)
    intraday = fetch_candles(kite, instrument_token, "minute", from_date, to_date)
    return simulate(h1, m15, intraday, capital, m15_filter, **trail_kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["yfinance", "kite"], default="yfinance")
    parser.add_argument("--symbol", help="yfinance ticker, e.g. RELIANCE.NS or ^NSEI (source=yfinance)")
    parser.add_argument("--period", default="30d", help="yfinance lookback, e.g. 30d, 60d (source=yfinance)")
    parser.add_argument("--token", type=int, help="Kite instrument_token (source=kite)")
    parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD (source=kite)")
    parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD (source=kite)")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--m15-filter", action="store_true")
    parser.add_argument("--breakeven-trigger", type=float, default=5.0,
                         help="profit (price units) to move stop to breakeven+offset")
    parser.add_argument("--breakeven-offset", type=float, default=1.0)
    parser.add_argument("--trail-trigger", type=float, default=10.0,
                         help="profit (price units) to trail stop to entry+trail-offset")
    parser.add_argument("--trail-offset", type=float, default=5.0)
    args = parser.parse_args()

    trail_kwargs = dict(
        breakeven_trigger=args.breakeven_trigger, breakeven_offset=args.breakeven_offset,
        trail_trigger=args.trail_trigger, trail_offset=args.trail_offset,
    )

    if args.source == "yfinance":
        if not args.symbol:
            parser.error("--symbol is required for --source yfinance")
        result = run_backtest_yfinance(args.symbol, args.period, args.capital, args.m15_filter, **trail_kwargs)
    else:
        if not (args.token and args.from_date and args.to_date):
            parser.error("--token, --from, and --to are required for --source kite")
        result = run_backtest_kite(
            args.token,
            datetime.strptime(args.from_date, "%Y-%m-%d"),
            datetime.strptime(args.to_date, "%Y-%m-%d"),
            args.capital, args.m15_filter, **trail_kwargs,
        )

    print(f"Trades: {len(result.trade_log)}  Total P&L: {result.cash_pnl:.2f}")
    for t in result.trade_log:
        print(t)
