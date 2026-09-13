"""
Live/paper runner: polls Kite for the day's H1/M15 opens and the latest LTP,
driving the strategy in real time.

SAFETY: defaults to paper trading — every "entry" is simulated by
PaperBroker and printed; nothing touches your Zerodha account. Real orders
require BOTH `--live` on the command line AND `MONEYMAKING_LIVE=true` in the
environment — two separate, deliberate opt-ins.

Usage:
    python run_live.py --token 256265 --symbol NIFTY --exchange NSE
    python run_live.py --token <fut_token> --symbol NIFTY24SEPFUT --exchange NFO --live
"""
import argparse
import os
import time as time_module
from datetime import date, datetime

from kite_client import get_kite_client, place_order
from data import fetch_candles, MARKET_OPEN
from strategy import HighLowOpenStrategy
from paper_broker import PaperBroker
from risk import RiskManager

POLL_SECONDS = 15


def market_open_today() -> datetime:
    return datetime.combine(date.today(), MARKET_OPEN)


def run(instrument_token: int, tradingsymbol: str, exchange: str, capital: float,
        m15_filter: bool, live: bool) -> None:
    if live and os.environ.get("MONEYMAKING_LIVE") != "true":
        raise SystemExit("Refusing to trade live: set MONEYMAKING_LIVE=true in the environment first.")

    kite = get_kite_client()
    strat = HighLowOpenStrategy(m15_filter=m15_filter)
    broker = PaperBroker()
    risk = RiskManager(capital=capital)
    strat.reset_session()

    seen_h1_opens: set = set()
    print(f"{'LIVE — REAL ORDERS' if live else 'PAPER (simulated)'} mode — {tradingsymbol} — Ctrl+C to stop")

    while True:
        now = datetime.now()
        start = market_open_today()
        h1 = fetch_candles(kite, instrument_token, "60minute", start, now)
        m15 = fetch_candles(kite, instrument_token, "15minute", start, now)

        for bar in h1:
            if bar["date"] not in seen_h1_opens:
                strat.on_bar_open(bar["open"])
                seen_h1_opens.add(bar["date"])

        quote_key = f"{exchange}:{tradingsymbol}"
        ltp = kite.ltp(quote_key)[quote_key]["last_price"]
        m15_open = m15[-1]["open"] if m15 else None

        if broker.in_position:
            pnl = broker.on_price(ltp)
            if pnl is not None:
                risk.record_trade(pnl)
                print(f"Exit @ {ltp}  P&L {pnl:.2f}  (session P&L {risk.realized_pnl_today:.2f})")
        elif risk.trading_halted():
            print("Daily loss limit hit — halted for the session.")
        else:
            signal = strat.on_price(ltp, m15_open)
            if signal:
                qty = risk.position_size(signal.entry_price, signal.stop_loss)
                if qty > 0:
                    print(f"Signal: {signal.side.value} @ {signal.entry_price} "
                          f"stop {signal.stop_loss} qty {qty} ({signal.reason})")
                    broker.enter(signal.side, signal.entry_price, qty, signal.stop_loss)
                    if live:
                        place_order(
                            kite, tradingsymbol=tradingsymbol, exchange=exchange,
                            transaction_type="BUY" if signal.side.value == "LONG" else "SELL",
                            quantity=qty, product=kite.PRODUCT_MIS,
                        )

        time_module.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", type=int, required=True, help="Kite instrument_token")
    parser.add_argument("--symbol", required=True, help="Trading symbol, e.g. NIFTY24SEPFUT")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--m15-filter", action="store_true")
    parser.add_argument("--live", action="store_true", help="Place REAL orders (also requires MONEYMAKING_LIVE=true)")
    args = parser.parse_args()
    run(args.token, args.symbol, args.exchange, args.capital, args.m15_filter, args.live)
