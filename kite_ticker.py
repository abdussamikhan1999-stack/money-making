"""
Real-time LTP streaming via Kite's own WebSocket API (KiteTicker) — an
alternative to run_live.py's default 15-second polling loop.

This is an original implementation using Zerodha's own official KiteTicker
class (part of the `kiteconnect` package this repo already depends on), NOT
copied from any third-party project. A well-known open-source algo trading
platform (OpenAlgo, AGPL-3.0 licensed) reimplements the raw WebSocket
protocol itself for its own reasons — scaling to 1800+ symbols across many
hosted users, avoiding an asyncio/eventlet conflict in its particular server
stack — neither of which applies to a personal, single-instrument bot.
KiteTicker already handles that complexity for a normal use case.
"""
import threading

from kiteconnect import KiteTicker


class TickStream:
    """Wraps KiteTicker to push live LTP updates for ONE instrument to a
    callback, replacing polling with real-time pushes. KiteTicker itself
    already handles reconnection; this class just tracks whether the
    initial connection succeeded and filters ticks to the instrument
    this stream cares about.

    Note: Kite access tokens roll over daily. If this process might run
    across a rollover, re-create the TickStream with a fresh token rather
    than expecting a long-lived one to keep working — this class does not
    attempt to auto-refresh it.
    """

    def __init__(self, api_key: str, access_token: str, instrument_token: int, on_ltp):
        self.instrument_token = instrument_token
        self.on_ltp = on_ltp
        self._kws = KiteTicker(api_key, access_token)
        self._kws.on_ticks = self._handle_ticks
        self._kws.on_connect = self._handle_connect
        self._connected = threading.Event()

    def _handle_connect(self, ws, response) -> None:
        ws.subscribe([self.instrument_token])
        ws.set_mode(ws.MODE_LTP, [self.instrument_token])
        self._connected.set()

    def _handle_ticks(self, ws, ticks: list[dict]) -> None:
        for tick in ticks:
            if tick.get("instrument_token") == self.instrument_token:
                self.on_ltp(tick["last_price"])

    def start(self, wait_for_connect: float = 10.0) -> bool:
        """Starts streaming in a background thread. Returns True once
        connected, False if it didn't connect within wait_for_connect
        seconds — KiteTicker retries internally regardless; this just
        gives the caller an initial connected/not-yet-connected signal."""
        self._kws.connect(threaded=True)
        return self._connected.wait(timeout=wait_for_connect)

    def stop(self) -> None:
        self._kws.close()
