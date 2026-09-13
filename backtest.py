"""
Backtest the Highest-Open/Lowest-Open strategy against Kite historical data.

Usage:
    python backtest.py --token 256265 --from 2026-08-01 --to 2026-09-01

`--token` is a Kite instrument_token — look one up with
`kite.instruments("NSE")` / `kite.instruments("NFO")` (e.g. 256265 is the
NIFTY 50 index). Requires KITE_API_KEY + KITE_ACCESS_TOKEN in the
environment (see README.md / .env.example).

CAVEAT — this is an OHLC-order approximation, not a true tick backtest:
within each minute candle, prices are fed to the strategy in the order
open -> high -> low -> close. The real intra-minute path is unknown from
historical OHLC alone, so this can misorder which of a minute's high/low
came first. It's a reasonable approximation, not a guarantee — validate
further (e.g. against tick data, or in paper mode) before trusting results.
"""
import argparse
from datetime import datetime

from kite_client import get_kite_client
from data import fetch_candles
from strategy import HighLowOpenStrategy
from paper_broker import PaperBroker
from risk import RiskManager


def run_backtest(instrument_token: int, from_date: datetime, to_date: datetime,
                  capital: float = 100_000.0, m15_filter: bool = False) -> PaperBroker:
    kite = get_kite_client()
    h1 = fetch_candles(kite, instrument_token, "60minute", from_date, to_date)
    m15 = fetch_candles(kite, instrument_token, "15minute", from_date, to_date)
    m1 = fetch_candles(kite, instrument_token, "minute", from_date, to_date)

    m15_open_at = {c["date"]: c["open"] for c in m15}
    minutes_by_hour: dict = {}
    for c in m1:
        hour_bucket = c["date"].replace(minute=0, second=0, microsecond=0)
        minutes_by_hour.setdefault(hour_bucket, []).append(c)

    def m15_open_for(ts: datetime) -> float | None:
        bucket = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        return m15_open_at.get(bucket)

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

        for minute in minutes_by_hour.get(bar["date"], []):
            for px in (minute["open"], minute["high"], minute["low"], minute["close"]):
                if broker.in_position:
                    pnl = broker.on_price(px)
                    if pnl is not None:
                        risk.record_trade(pnl)
                    continue
                if risk.trading_halted():
                    continue
                signal = strat.on_price(px, m15_open_for(minute["date"]))
                if signal:
                    qty = risk.position_size(signal.entry_price, signal.stop_loss)
                    if qty > 0:
                        broker.enter(signal.side, signal.entry_price, qty, signal.stop_loss)

    return broker


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", type=int, required=True, help="Kite instrument_token")
    parser.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--m15-filter", action="store_true")
    args = parser.parse_args()

    result = run_backtest(
        args.token,
        datetime.strptime(args.from_date, "%Y-%m-%d"),
        datetime.strptime(args.to_date, "%Y-%m-%d"),
        capital=args.capital,
        m15_filter=args.m15_filter,
    )
    print(f"Trades: {len(result.trade_log)}  Total P&L: {result.cash_pnl:.2f}")
    for t in result.trade_log:
        print(t)
