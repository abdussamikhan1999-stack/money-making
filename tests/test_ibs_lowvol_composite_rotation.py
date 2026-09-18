import datetime

from probe_ibs_lowvol_composite_rotation import _zscore, rank_composite, trailing_volatility


def _series(prices):
    return [dict(date=datetime.datetime(2024, 1, 1) + datetime.timedelta(days=i),
                 open=p, high=p * 1.02, low=p * 0.98, close=p) for i, p in enumerate(prices)]


def _ohlc_series(bars):
    """bars: list of (high, low, close) -> controls IBS directly, independent
    of trailing_volatility (which only reads closes)."""
    return [dict(date=datetime.datetime(2024, 1, 1) + datetime.timedelta(days=i),
                 open=c, high=h, low=l, close=c) for i, (h, l, c) in enumerate(bars)]


def test_zscore_handles_degenerate_input():
    assert _zscore([]) == []
    assert _zscore([5.0]) == [0.0]
    assert _zscore([3.0, 3.0, 3.0]) == [0.0, 0.0, 0.0]


def test_trailing_volatility_needs_full_lookback():
    dates = [datetime.date(2024, 1, i + 1) for i in range(10)]
    closes = [100 + i for i in range(10)]
    assert trailing_volatility(dates, closes, dates[5], lookback=20) is None
    assert trailing_volatility(dates, closes, dates[9], lookback=5) is not None


def test_vol_weight_zero_matches_pure_ibs_ranking():
    # A: close sits near the day's HIGH every day (IBS ~0.9, not oversold).
    # B: close sits near the day's LOW every day (IBS ~0.1, oversold). At
    # vol_weight=0.0, volatility must be ignored entirely -> B ranks first.
    a_bars = [(110, 100, 109) for _ in range(8)]
    b_bars = [(110, 100, 101) for _ in range(8)]
    series = {"A": _ohlc_series(a_bars), "B": _ohlc_series(b_bars)}
    dates_map = {s: [c["date"].date() for c in cds] for s, cds in series.items()}
    closes_map = {s: [c["close"] for c in cds] for s, cds in series.items()}
    as_of = dates_map["A"][-1]

    ranked = rank_composite(series, dates_map, closes_map, as_of, ibs_lookback=3,
                             vol_lookback=5, top_k=2, vol_weight=0.0)
    assert [sym for _, sym, _ in ranked][0] == "B"


def test_vol_weight_one_ranks_by_volatility_only():
    calm = [100, 100.2, 100.4, 100.6, 100.8, 101.0, 101.2, 101.4]
    wild = [100, 120, 90, 130, 80, 125, 85, 115]
    series = {"CALM": _series(calm), "WILD": _series(wild)}
    dates_map = {s: [c["date"].date() for c in cds] for s, cds in series.items()}
    closes_map = {s: [c["close"] for c in cds] for s, cds in series.items()}
    as_of = dates_map["CALM"][-1]

    ranked = rank_composite(series, dates_map, closes_map, as_of, ibs_lookback=3,
                             vol_lookback=5, top_k=2, vol_weight=1.0)
    assert [sym for _, sym, _ in ranked][0] == "CALM"
