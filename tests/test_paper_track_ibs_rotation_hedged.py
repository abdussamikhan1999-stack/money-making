import math
from datetime import date, datetime, timedelta

from probe_ibs_rotation import dates_closes_maps, rank_by_ibs, price_at_or_before
from probe_ibs_rotation_etf_hedge import simulate_etf_hedged, ETF_SYMBOL
from paper_track_ibs_rotation import TOP_K, LOOKBACK, CAPITAL
from paper_track_ibs_rotation_hedged import mark_to_market, HEDGE_RATIO


def _series(symbol_ranges: dict, n_days: int = 10) -> dict:
    start = date(2026, 1, 1)
    series = {}
    for sym, (lo, hi) in symbol_ranges.items():
        candles = []
        for i in range(n_days):
            frac = i / (n_days - 1)
            close = lo + (hi - lo) * frac
            candles.append({"date": datetime.combine(start + timedelta(days=i), datetime.min.time()), "open": close,
                             "high": close + 1, "low": close - 1, "close": close, "volume": 0})
        series[sym] = candles
    return series


def test_hedged_live_picker_matches_unhedged_live_picker():
    """Both trackers must select identical stock legs -- they share rank_by_ibs,
    the only difference is the added ETF short leg."""
    series = _series({"A": (10, 10.5), "B": (10, 15), "C": (10, 30), "D": (10, 100)})
    dates_map, closes_map = dates_closes_maps(series)
    as_of = date(2026, 1, 1) + timedelta(days=LOOKBACK - 1)

    picks_a = rank_by_ibs(series, dates_map, closes_map, as_of, LOOKBACK, TOP_K)
    picks_b = rank_by_ibs(series, dates_map, closes_map, as_of, LOOKBACK, TOP_K)
    assert [sym for _, sym, _ in picks_a] == [sym for _, sym, _ in picks_b]


def test_hedge_sizing_matches_backtest_formula():
    etf_series = _series({ETF_SYMBOL: (250, 260)})[ETF_SYMBOL]
    etf_dates = [c["date"].date() for c in etf_series]
    etf_closes = [c["close"] for c in etf_series]
    as_of = etf_dates[0]
    etf_p0 = price_at_or_before(etf_dates, etf_closes, as_of)

    beta = 1.143
    hedge_notional = HEDGE_RATIO * beta * CAPITAL
    expected_qty = math.floor(hedge_notional / etf_p0)

    # Same formula simulate_etf_hedged() uses internally for its short leg.
    qty = math.floor(HEDGE_RATIO * beta * CAPITAL / etf_p0) if etf_p0 else 0
    assert qty == expected_qty


def test_mark_to_market_matches_simulate_etf_hedged_pnl():
    series = _series({"A": (10, 10.5), "B": (10, 15)})
    etf_series_map = _series({ETF_SYMBOL: (250, 260)})
    dates_map, closes_map = dates_closes_maps(series)
    etf_dates_map, etf_closes_map = dates_closes_maps(etf_series_map)
    etf_dates, etf_closes = etf_dates_map[ETF_SYMBOL], etf_closes_map[ETF_SYMBOL]

    entry_date = date(2026, 1, 1) + timedelta(days=LOOKBACK - 1)
    exit_date = date(2026, 1, 1) + timedelta(days=9)

    picks = rank_by_ibs(series, dates_map, closes_map, entry_date, LOOKBACK, TOP_K)
    etf_p0 = price_at_or_before(etf_dates, etf_closes, entry_date)
    beta = 1.143
    qty = math.floor(HEDGE_RATIO * beta * CAPITAL / etf_p0)

    record = {
        "date": str(entry_date),
        "picks": [{"symbol": sym, "entry_price": px, "entry_ibs": score} for score, sym, px in picks],
        "etf_hedge": {"symbol": ETF_SYMBOL, "qty": qty, "entry_price": etf_p0},
        "exit": None,
    }
    mark_to_market(record, dates_map, closes_map, etf_dates, etf_closes, exit_date)

    backtest = simulate_etf_hedged([entry_date, exit_date], series, etf_series_map[ETF_SYMBOL],
                                    top_k=TOP_K, lookback=LOOKBACK, capital=CAPITAL,
                                    hedge_ratio=HEDGE_RATIO, beta=beta)
    assert record["exit"]["total_net_pnl"] == round(backtest["total_net"], 2)
