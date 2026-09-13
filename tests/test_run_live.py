from run_live import Trader
from strategy import HighLowOpenStrategy
from paper_broker import PaperBroker
from risk import RiskManager


def make_trader(live=False):
    strat = HighLowOpenStrategy()
    strat.reset_session()
    return Trader(strat, PaperBroker(), RiskManager(capital=100_000), "TESTSYM", "NSE", live, kite=None)


def test_on_ltp_enters_paper_position_on_signal():
    trader = make_trader()
    trader.strat.on_bar_open(100)
    trader.on_ltp(105)  # arms short
    assert not trader.broker.in_position
    trader.on_ltp(100)  # fires short
    assert trader.broker.in_position


def test_on_ltp_never_calls_place_order_in_paper_mode():
    calls = []
    trader = make_trader(live=False)

    import run_live
    original = run_live.place_order
    run_live.place_order = lambda *a, **k: calls.append((a, k))
    try:
        trader.strat.on_bar_open(100)
        trader.on_ltp(105)
        trader.on_ltp(100)
    finally:
        run_live.place_order = original

    assert calls == []  # paper mode must never touch the real broker


def test_on_ltp_closes_position_and_records_pnl():
    trader = make_trader()
    trader.strat.on_bar_open(100)
    trader.on_ltp(105)
    trader.on_ltp(100)  # short entered, stop = day high (105)
    assert trader.broker.in_position
    trader.on_ltp(105)  # stopped out
    assert not trader.broker.in_position
    assert trader.risk.realized_pnl_today != 0


def test_on_ltp_does_nothing_once_daily_loss_halted():
    trader = make_trader()
    trader.risk.record_trade(-3000)  # exceeds default 2% of 100k
    trader.strat.on_bar_open(100)
    trader.on_ltp(105)
    trader.on_ltp(100)
    assert not trader.broker.in_position  # halted before ever entering
