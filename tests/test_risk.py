from risk import RiskManager


def test_position_size_scales_with_stop_distance():
    r = RiskManager(capital=100_000, risk_per_trade_pct=1.0, lot_size=1)
    # risk budget = 1000; stop distance 10 -> 100 units
    assert r.position_size(entry_price=100, stop_loss=90) == 100


def test_position_size_rounds_down_to_whole_lots():
    r = RiskManager(capital=100_000, risk_per_trade_pct=1.0, lot_size=75)
    # risk budget = 1000; stop distance 10 -> raw 100 units -> 1 lot of 75
    assert r.position_size(entry_price=100, stop_loss=90) == 75


def test_position_size_zero_when_no_stop_distance():
    r = RiskManager(capital=100_000)
    assert r.position_size(entry_price=100, stop_loss=100) == 0


def test_daily_loss_halts_trading():
    r = RiskManager(capital=100_000, max_daily_loss_pct=2.0)
    assert r.trading_halted() is False
    r.record_trade(-1500)
    assert r.trading_halted() is False
    r.record_trade(-600)
    assert r.trading_halted() is True


def test_reset_day_clears_pnl():
    r = RiskManager(capital=100_000, max_daily_loss_pct=2.0)
    r.record_trade(-3000)
    assert r.trading_halted() is True
    r.reset_day()
    assert r.trading_halted() is False
    assert r.realized_pnl_today == 0.0
