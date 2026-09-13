"""Position sizing and a daily-loss circuit breaker. Pure, no I/O."""
from dataclasses import dataclass, field


@dataclass
class RiskManager:
    capital: float
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    lot_size: int = 1  # e.g. an F&O lot size; leave at 1 for equity/index share-quantity sizing

    _realized_pnl_today: float = field(default=0.0, init=False)

    @property
    def realized_pnl_today(self) -> float:
        return self._realized_pnl_today

    def position_size(self, entry_price: float, stop_loss: float) -> int:
        """Quantity such that a stop-out risks ~risk_per_trade_pct of capital, rounded down to whole lots."""
        risk_amount = self.capital * (self.risk_per_trade_pct / 100)
        per_unit_risk = abs(entry_price - stop_loss)
        if per_unit_risk <= 0:
            return 0
        raw_qty = risk_amount / per_unit_risk
        lots = max(int(raw_qty // self.lot_size), 0)
        return lots * self.lot_size

    def record_trade(self, pnl: float) -> None:
        self._realized_pnl_today += pnl

    def trading_halted(self) -> bool:
        return self._realized_pnl_today <= -(self.capital * self.max_daily_loss_pct / 100)

    def reset_day(self) -> None:
        self._realized_pnl_today = 0.0
