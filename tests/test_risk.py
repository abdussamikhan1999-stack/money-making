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


def test_reset_day_does_not_clear_cumulative_drawdown_halt():
    """This is the exact gap the SI=F backtest exposed: a strategy losing a
    little every day, reset daily, must still get stopped by cumulative
    drawdown — reset_day() must never paper over that."""
    r = RiskManager(capital=100_000, max_daily_loss_pct=50.0, max_drawdown_pct=10.0)
    for _ in range(25):
        r.record_trade(-500)  # a small loss each "day", never trips the daily breaker alone
        r.reset_day()
    assert r.drawdown_halted is True
    assert r.trading_halted() is True


def test_drawdown_tracks_from_peak_not_from_starting_capital():
    r = RiskManager(capital=100_000, max_drawdown_pct=10.0)
    r.record_trade(5000)   # equity now 105,000 — new peak
    r.record_trade(-9000)  # down to 96,000: that's a 8,571 drop from peak (105k), not 4k from start
    assert r.drawdown_halted is False
    r.record_trade(-2000)  # down to 94,000: 10.5% below the 105k peak -> trips
    assert r.drawdown_halted is True


def test_reset_capital_clears_everything():
    r = RiskManager(capital=100_000, max_drawdown_pct=10.0)
    r.record_trade(-15000)
    assert r.drawdown_halted is True
    r.reset_capital()
    assert r.drawdown_halted is False
    assert r.equity == 100_000


def test_volatility_position_size_scales_with_atr():
    r = RiskManager(capital=100_000, risk_per_trade_pct=1.0, lot_size=1)
    # risk budget = 1000; ATR of 20 -> 50 units
    assert r.volatility_position_size(atr=20) == 50


def test_volatility_position_size_zero_for_nonpositive_atr():
    r = RiskManager(capital=100_000)
    assert r.volatility_position_size(atr=0) == 0
