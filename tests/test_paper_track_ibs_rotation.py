from datetime import date, datetime, time, timedelta

from probe_ibs_rotation import dates_closes_maps, rank_by_ibs, simulate, latest_settled_date
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


def _calendar(*dates: date) -> list[dict]:
    return [{"date": datetime.combine(d, datetime.min.time())} for d in dates]


def test_latest_settled_date_drops_todays_still_forming_bar_during_market_hours():
    """entry 49: yfinance's "today" 1d bar keeps changing while NSE is
    open (09:15-15:30 IST), so two live-tracker runs minutes apart
    computed different IBS scores from the same "shared" ranking code —
    not the retry noise it was first assumed to be."""
    cal = _calendar(date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17))
    mid_session = datetime.combine(date(2026, 9, 17), time(15, 10))
    assert latest_settled_date(cal, now=mid_session) == date(2026, 9, 16)


def test_latest_settled_date_uses_todays_bar_after_close():
    cal = _calendar(date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17))
    after_close = datetime.combine(date(2026, 9, 17), time(16, 0))
    assert latest_settled_date(cal, now=after_close) == date(2026, 9, 17)


def test_latest_settled_date_unaffected_on_a_non_current_last_bar():
    """Backtests always pass historical calendars whose last bar isn't
    "today" — must return it unchanged regardless of wall-clock time."""
    cal = _calendar(date(2020, 1, 1), date(2020, 1, 2))
    assert latest_settled_date(cal, now=datetime.combine(date(2026, 9, 17), time(12, 0))) == date(2020, 1, 2)


def test_horizon_returns_use_lag1_fill_and_report_excess_over_universe():
    from paper_track_ibs_rotation import horizon_returns
    d = [date(2026, 1, 1) + timedelta(days=i) for i in range(12)]
    dates_map = {"A": d, "B": d, "C": d}
    # record date = d[0]; lag-1 fill is d[1]. A doubles between d[1] and d[6] (h=5); B, C flat.
    closes_map = {"A": [100, 100, 100, 100, 100, 100, 200, 200, 200, 200, 200, 200],
                  "B": [100.0] * 12, "C": [100.0] * 12}
    rec = {"date": "2026-01-01", "picks": [{"symbol": "A"}]}
    out = horizon_returns(rec, dates_map, closes_map, hs=(5, 10, 30))
    assert abs(out["5"]["picks_mean"] - 1.0) < 1e-9 and abs(out["5"]["universe_mean"] - 1 / 3) < 1e-4
    assert abs(out["5"]["excess"] - (1.0 - 1 / 3)) < 1e-4
    assert "30" not in out  # horizon not yet elapsed is omitted, never guessed
