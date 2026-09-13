from paper_broker import PaperBroker
from strategy import Side


def test_long_trade_stopped_out_for_a_loss():
    b = PaperBroker()
    b.enter(Side.LONG, price=100, quantity=10, stop_loss=95)
    assert b.in_position is True
    assert b.on_price(98) is None  # still above stop
    pnl = b.on_price(95)
    assert pnl == -50  # (95-100)*10
    assert b.in_position is False
    assert b.cash_pnl == -50


def test_long_trade_trails_into_profit():
    b = PaperBroker()
    b.enter(Side.LONG, price=100, quantity=10, stop_loss=95,
            breakeven_trigger=5, breakeven_offset=1, trail_trigger=10, trail_offset=5)
    b.on_price(106)   # profit 6 -> stop to 101
    b.on_price(111)   # profit 11 -> stop to 105
    pnl = b.on_price(104)  # falls back through trailed stop
    assert pnl == 50  # (105-100)*10
    assert b.cash_pnl == 50


def test_short_trade_pnl_sign():
    b = PaperBroker()
    b.enter(Side.SHORT, price=100, quantity=10, stop_loss=105)
    pnl = b.on_price(105)
    assert pnl == -50  # (100-105)*10


def test_commission_deducted_from_net_pnl_but_not_gross():
    b = PaperBroker(commission_per_trade=4.0)
    b.enter(Side.LONG, price=100, quantity=10, stop_loss=95)
    pnl = b.on_price(95)
    assert pnl == -54  # gross -50, minus the flat 4 commission
    assert b.trade_log[0]["gross_pnl"] == -50
    assert b.trade_log[0]["pnl"] == -54
    assert b.cash_pnl == -54
