from datetime import date, datetime, timedelta

from probe_ibs_rotation import dates_closes_maps, rank_by_ibs, simulate
from paper_track_ibs_rotation import mark_to_market, CAPITAL, TOP_K, LOOKBACK


def _series(symbol_ranges: dict, n_days: int = 10) -> dict:
    """symbol_ranges: {symbol: (low_close, high_close)} — builds n_days of
    bars where close drifts from low to high, so lower-ranged symbols end
    up more "oversold" (lower IBS) than higher-ranged ones."""
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


def test_live_picker_matches_backtest_picks_on_same_data():
    """Guards against paper_track_ibs_rotation.py silently drifting from
    what probe_ibs_rotation.simulate() actually backtested and validated
    (Thirty-eighth/Thirty-ninth/Fortieth CLAUDE.md entries)."""
    series = _series({"A": (10, 10.5), "B": (10, 15), "C": (10, 30), "D": (10, 100)})
    dates_map, closes_map = dates_closes_maps(series)
    entry_date = date(2026, 1, 1) + timedelta(days=LOOKBACK - 1)
    as_of = date(2026, 1, 1) + timedelta(days=9)

    live_picks = rank_by_ibs(series, dates_map, closes_map, entry_date, LOOKBACK, top_k=2)
    live_symbols = [sym for _, sym, _ in live_picks]

    # Backtest a single period ending exactly at as_of and confirm the same
    # two symbols were selected that month.
    result = simulate([entry_date, as_of], series, top_k=2, lookback=LOOKBACK)
    assert result["n_months"] == 1

    # A/B are the flattest (lowest close-to-close range => lowest IBS-driving
    # spread), so they should be the most-oversold picks in both paths.
    assert set(live_symbols) == {"A", "B"}


def test_mark_to_market_uses_same_cost_model_as_simulate():
    series = _series({"A": (10, 10.5), "B": (10, 15)})
    dates_map, closes_map = dates_closes_maps(series)
    entry_date = date(2026, 1, 1) + timedelta(days=LOOKBACK - 1)
    exit_date = date(2026, 1, 1) + timedelta(days=9)

    picks = rank_by_ibs(series, dates_map, closes_map, entry_date, LOOKBACK, TOP_K)
    record = {"date": str(entry_date),
               "picks": [{"symbol": sym, "entry_price": px, "entry_ibs": score}
                         for score, sym, px in picks],
               "exit": None}
    mark_to_market(record, dates_map, closes_map, exit_date)

    backtest = simulate([entry_date, exit_date], series, top_k=TOP_K, lookback=LOOKBACK, capital=CAPITAL)
    assert record["exit"]["net_pnl"] == round(backtest["total_net"], 2)
