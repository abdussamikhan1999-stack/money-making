"""
Live/paper runner: drives the strategy against the day's H1/M15 opens and
the latest price. Two modes for the price feed:
  - default: polls Kite every POLL_SECONDS (15s) for both candles and LTP.
  - --stream: refreshes H1/M15 candles on a slower interval
    (CANDLE_REFRESH_SECONDS — candles don't change every second) but reacts
    to price in real time via Kite's own WebSocket (KiteTicker, see
    kite_ticker.py) instead of polling for it. This matters because entries
    fire on every price update, not bar close (per the strategy's own
    rule) — streaming catches the exact first touch of a line instead of
    lagging by up to one poll interval.

SAFETY: defaults to paper trading — every "entry" is simulated by
PaperBroker and printed; nothing touches your Zerodha account. Real orders
require BOTH `--live` on the command line AND `MONEYMAKING_LIVE=true` in the
environment — two separate, deliberate opt-ins.

Usage:
    python run_live.py --token 256265 --symbol NIFTY --exchange NSE
    python run_live.py --token 256265 --symbol NIFTY --exchange NSE --stream
    python run_live.py --token <fut_token> --symbol NIFTY24SEPFUT --exchange NFO --live

NOTE: the --stream path is written against KiteTicker's documented API but,
like the rest of this repo's Kite integration, has not been exercised
against a live connection (no Kite Connect subscription in the environment
this was built in). The default polling path is the better-exercised one.
"""
import argparse
import os
import threading
import time as time_module
from datetime import date, datetime

from kite_client import get_kite_client, place_order
from kite_ticker import TickStream
from data import fetch_candles, MARKET_OPEN
from strategy import HighLowOpenStrategy
from paper_broker import PaperBroker
from risk import RiskManager

POLL_SECONDS = 15
CANDLE_REFRESH_SECONDS = 60


def market_open_today() -> datetime:
    return datetime.combine(date.today(), MARKET_OPEN)


class Trader:
    """Holds the shared strategy/broker/risk state and the single method
    (on_ltp) that decides what to do with a new price. Called from either
    the polling loop or the streaming callback — the lock matters only for
    --stream, where on_ltp runs on KiteTicker's background thread while
    candle refreshes happen on the main thread."""

    def __init__(self, strat: HighLowOpenStrategy, broker: PaperBroker, risk: RiskManager,
                 tradingsymbol: str, exchange: str, live: bool, kite) -> None:
        self.strat = strat
        self.broker = broker
        self.risk = risk
        self.tradingsymbol = tradingsymbol
        self.exchange = exchange
        self.live = live
        self.kite = kite
        self.lock = threading.Lock()
        self.latest_m15_open: float | None = None

    def refresh_candles(self, instrument_token: int, seen_h1_opens: set) -> None:
        now = datetime.now()
        start = market_open_today()
        h1 = fetch_candles(self.kite, instrument_token, "60minute", start, now)
        m15 = fetch_candles(self.kite, instrument_token, "15minute", start, now)
        with self.lock:
            for bar in h1:
                if bar["date"] not in seen_h1_opens:
                    self.strat.on_bar_open(bar["open"])
                    seen_h1_opens.add(bar["date"])
            self.latest_m15_open = m15[-1]["open"] if m15 else None

    def on_ltp(self, ltp: float) -> None:
        with self.lock:
            if self.broker.in_position:
                pnl = self.broker.on_price(ltp)
                if pnl is not None:
                    self.risk.record_trade(pnl)
                    print(f"Exit @ {ltp}  P&L {pnl:.2f}  (session P&L {self.risk.realized_pnl_today:.2f})")
                return
            if self.risk.trading_halted():
                print("Daily loss limit hit — halted for the session.")
                return
            signal = self.strat.on_price(ltp, self.latest_m15_open)
            if not signal:
                return
            qty = self.risk.position_size(signal.entry_price, signal.stop_loss)
            if qty <= 0:
                return
            print(f"Signal: {signal.side.value} @ {signal.entry_price} "
                  f"stop {signal.stop_loss} qty {qty} ({signal.reason})")
            self.broker.enter(signal.side, signal.entry_price, qty, signal.stop_loss)
            if self.live:
                place_order(
                    self.kite, tradingsymbol=self.tradingsymbol, exchange=self.exchange,
                    transaction_type="BUY" if signal.side.value == "LONG" else "SELL",
                    quantity=qty, product=self.kite.PRODUCT_MIS,
                )


def run(instrument_token: int, tradingsymbol: str, exchange: str, capital: float,
        m15_filter: bool, live: bool, stream: bool) -> None:
    if live and os.environ.get("MONEYMAKING_LIVE") != "true":
        raise SystemExit("Refusing to trade live: set MONEYMAKING_LIVE=true in the environment first.")

    kite = get_kite_client()
    strat = HighLowOpenStrategy(m15_filter=m15_filter)
    strat.reset_session()
    trader = Trader(strat, PaperBroker(), RiskManager(capital=capital), tradingsymbol, exchange, live, kite)
    seen_h1_opens: set = set()

    mode = "LIVE — REAL ORDERS" if live else "PAPER (simulated)"
    feed = "streaming (KiteTicker)" if stream else f"polling every {POLL_SECONDS}s"
    print(f"{mode} — {tradingsymbol} — price feed: {feed} — Ctrl+C to stop")

    trader.refresh_candles(instrument_token, seen_h1_opens)

    if stream:
        ticker = TickStream(os.environ["KITE_API_KEY"], os.environ["KITE_ACCESS_TOKEN"],
                             instrument_token, on_ltp=trader.on_ltp)
        if not ticker.start():
            raise SystemExit("Could not connect to Kite's WebSocket within 10s — check the access token/network.")
        try:
            while True:
                time_module.sleep(CANDLE_REFRESH_SECONDS)
                trader.refresh_candles(instrument_token, seen_h1_opens)
        finally:
            ticker.stop()
    else:
        while True:
            trader.refresh_candles(instrument_token, seen_h1_opens)
            quote_key = f"{exchange}:{tradingsymbol}"
            ltp = kite.ltp(quote_key)[quote_key]["last_price"]
            trader.on_ltp(ltp)
            time_module.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", type=int, required=True, help="Kite instrument_token")
    parser.add_argument("--symbol", required=True, help="Trading symbol, e.g. NIFTY24SEPFUT")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--m15-filter", action="store_true")
    parser.add_argument("--stream", action="store_true",
                         help="use real-time KiteTicker WebSocket for price instead of 15s polling")
    parser.add_argument("--live", action="store_true", help="Place REAL orders (also requires MONEYMAKING_LIVE=true)")
    args = parser.parse_args()
    run(args.token, args.symbol, args.exchange, args.capital, args.m15_filter, args.live, args.stream)
