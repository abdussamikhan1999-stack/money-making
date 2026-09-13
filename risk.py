"""Position sizing and risk circuit breakers. Pure, no I/O.

Two independent breakers, deliberately layered (Carver's "risk management as
a system, not an afterthought" — a single daily-loss check isn't enough):
  - `max_daily_loss_pct`: resets every day. Catches one bad day.
  - `max_drawdown_pct`: cumulative, across the strategy's whole lifetime.
    Never resets on its own — this is the one that was MISSING before and
    let a backtest run silently past what would, in real trading, have been
    a blown account (a 60d silver backtest lost 61% of starting capital
    because nothing was tracking cumulative equity, only same-day P&L).
"""
from dataclasses import dataclass, field


@dataclass
class RiskManager:
    capital: float
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 10.0
    lot_size: int = 1  # e.g. an F&O lot size; leave at 1 for equity/index share-quantity sizing

    _realized_pnl_today: float = field(default=0.0, init=False)
    _cumulative_pnl: float = field(default=0.0, init=False)
    _peak_equity: float = field(default=0.0, init=False)
    _drawdown_halted: bool = field(default=False, init=False)

    @property
    def realized_pnl_today(self) -> float:
        return self._realized_pnl_today

    @property
    def equity(self) -> float:
        return self.capital + self._cumulative_pnl

    @property
    def drawdown_from_peak_pct(self) -> float:
        """How far current equity sits below its all-time peak, as a % of starting capital."""
        return max(0.0, (self._peak_equity - self._cumulative_pnl) / self.capital * 100)

    @property
    def drawdown_halted(self) -> bool:
        """True once the cumulative drawdown breaker has tripped. Distinct
        from a same-day halt — this one does NOT clear on reset_day()."""
        return self._drawdown_halted

    def position_size(self, entry_price: float, stop_loss: float) -> int:
        """Quantity such that a stop-out risks ~risk_per_trade_pct of capital, rounded down to whole lots."""
        risk_amount = self.capital * (self.risk_per_trade_pct / 100)
        per_unit_risk = abs(entry_price - stop_loss)
        if per_unit_risk <= 0:
            return 0
        raw_qty = risk_amount / per_unit_risk
        lots = max(int(raw_qty // self.lot_size), 0)
        return lots * self.lot_size

    def volatility_position_size(self, atr: float, risk_pct: float | None = None) -> int:
        """Carver-style volatility sizing: scale quantity to the instrument's
        own recent volatility (ATR) rather than the strategy's own stop
        distance. Use as a sanity cap alongside position_size() — e.g. take
        whichever is smaller — not as an automatic replacement, since the
        two answer different questions (what does the stop risk vs. what
        does typical movement risk)."""
        if atr <= 0:
            return 0
        pct = self.risk_per_trade_pct if risk_pct is None else risk_pct
        risk_amount = self.capital * (pct / 100)
        raw_qty = risk_amount / atr
        lots = max(int(raw_qty // self.lot_size), 0)
        return lots * self.lot_size

    def record_trade(self, pnl: float) -> None:
        self._realized_pnl_today += pnl
        self._cumulative_pnl += pnl
        self._peak_equity = max(self._peak_equity, self._cumulative_pnl)
        if self.drawdown_from_peak_pct >= self.max_drawdown_pct:
            self._drawdown_halted = True

    def trading_halted(self) -> bool:
        if self._drawdown_halted:
            return True
        return self._realized_pnl_today <= -(self.capital * self.max_daily_loss_pct / 100)

    def reset_day(self) -> None:
        """Resets only the DAILY loss tracker. Deliberately does NOT clear a
        drawdown halt — a blown account doesn't un-blow itself at midnight."""
        self._realized_pnl_today = 0.0

    def reset_capital(self) -> None:
        """Explicit, deliberate reset of ALL cumulative state — for starting
        an independent backtest segment, or a real decision to re-fund/
        restart after review. Never call this automatically from a runner."""
        self._realized_pnl_today = 0.0
        self._cumulative_pnl = 0.0
        self._peak_equity = 0.0
        self._drawdown_halted = False
