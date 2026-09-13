"""Simulated broker: fills instantly at the given price, runs the
TrailingStopManager, tracks P&L. No real orders are ever placed here — this
is the safe default for both backtest.py and run_live.py.

`commission_per_trade` defaults to 0 for backward compatibility, but leaving
it at 0 is what let an apparent "edge" on high-frequency silver-futures
trades (139+ trades per backtest half) look profitable when a realistic
~$4/round-trip cost made every configuration of it net-negative. Set this
to a real per-trade cost estimate for your instrument/broker before trusting
any backtest result, especially a high-trade-count one."""
from dataclasses import dataclass, field

from strategy import Side, TrailingStopManager


@dataclass
class PaperBroker:
    cash_pnl: float = 0.0
    commission_per_trade: float = 0.0
    trade_log: list = field(default_factory=list)
    _position: dict | None = field(default=None, init=False)
    _trail: TrailingStopManager | None = field(default=None, init=False)

    @property
    def in_position(self) -> bool:
        return self._position is not None

    def enter(self, side: Side, price: float, quantity: int, stop_loss: float,
              breakeven_trigger: float = 5.0, breakeven_offset: float = 1.0,
              trail_trigger: float = 10.0, trail_offset: float = 5.0) -> None:
        self._trail = TrailingStopManager(
            entry_price=price, initial_stop=stop_loss, side=side,
            breakeven_trigger=breakeven_trigger, breakeven_offset=breakeven_offset,
            trail_trigger=trail_trigger, trail_offset=trail_offset,
        )
        self._position = {"side": side, "entry": price, "qty": quantity}

    def on_price(self, ltp: float) -> float | None:
        """Feed every price update while in position. Updates the trailing
        stop and closes + returns realised P&L the instant it's hit."""
        if not self._position:
            return None
        self._trail.update(ltp)
        if self._trail.hit(ltp):
            return self.close(self._trail.stop)
        return None

    def close(self, exit_price: float) -> float:
        pos = self._position
        direction = 1 if pos["side"] == Side.LONG else -1
        gross_pnl = direction * (exit_price - pos["entry"]) * pos["qty"]
        pnl = gross_pnl - self.commission_per_trade
        self.cash_pnl += pnl
        self.trade_log.append({**pos, "side": pos["side"].value, "exit": exit_price,
                                "gross_pnl": gross_pnl, "pnl": pnl})
        self._position = None
        self._trail = None
        return pnl
