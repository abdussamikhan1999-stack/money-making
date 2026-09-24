import numpy as np
import pandas as pd

import probe_delivery_signal as ds
import probe_dividend_signal as dv


def cal_and_pos(start="2019-01-01", end="2021-12-31"):
    cal = pd.bdate_range(start, end)
    return cal, dv.month_end_positions(cal)


def test_month_end_positions_are_the_last_trading_day_of_each_month():
    cal, pos = cal_and_pos("2020-01-01", "2020-03-31")
    assert [cal[p].date().isoformat() for p in pos] == ["2020-01-31", "2020-02-28", "2020-03-31"]


def test_forward_return_enters_the_day_after_month_end_and_exits_h_month_ends_later():
    cal, pos = cal_and_pos("2020-01-01", "2020-06-30")
    A = pd.DataFrame({"X": np.arange(len(cal), dtype=float) + 100.0}, index=cal)
    f = dv.forward_monthly(A, pos, 1)
    d = 0                                                              # decision at end of Jan
    expect = A["X"].iloc[pos[1]] / A["X"].iloc[pos[0] + 1] - 1         # first day of Feb close -> end of Feb close
    assert abs(f["X"].iloc[d] - expect) < 1e-12
    assert np.isnan(f["X"].iloc[-1])                                    # no month-end h ahead of the last decision


def test_div1_predicts_next_month_from_the_same_month_a_year_earlier_and_dyld_window_is_365_days():
    cal, pos = cal_and_pos("2019-01-01", "2021-12-31")
    R = pd.DataFrame({"X": 100.0}, index=cal)
    divs = {"X": pd.Series([5.0, 2.0], index=pd.to_datetime(["2019-06-14", "2020-06-30"]))}
    s = dv.dividend_signals(R, divs, cal, pos)
    dates = cal[pos]
    d_may20 = list(dates).index(pd.Timestamp("2020-05-29"))            # decision end of May 2020 -> target June 2020
    d_jun20 = list(dates).index(pd.Timestamp("2020-06-30"))            # target July 2020: no July dividend in 2019
    assert s["DIV1"]["X"].iloc[d_may20] == 1.0 and s["DIV1"]["X"].iloc[d_jun20] == 0.0
    assert abs(s["DYLD"]["X"].iloc[d_jun20] - 0.02) < 1e-12            # only the 2020-06-30 dividend is inside (ME-365d, ME]
    d_jul19 = list(dates).index(pd.Timestamp("2019-07-31"))
    assert abs(s["DYLD"]["X"].iloc[d_jul19] - 0.05) < 1e-12            # 2019-06-14 inside the trailing year


def test_signals_at_a_month_use_no_dividend_after_that_month_end():
    cal, pos = cal_and_pos("2019-01-01", "2021-12-31")
    R = pd.DataFrame({"X": 100.0}, index=cal)
    a = dv.dividend_signals(R, {"X": pd.Series([5.0], index=pd.to_datetime(["2019-06-14"]))}, cal, pos)
    b = dv.dividend_signals(R, {"X": pd.Series([5.0, 9.0, 9.0], index=pd.to_datetime(["2019-06-14", "2020-09-10", "2021-03-10"]))}, cal, pos)
    cut = list(cal[pos]).index(pd.Timestamp("2020-08-31"))             # everything through end of Aug 2020 must be identical
    for k in a:
        pd.testing.assert_frame_equal(a[k].iloc[:cut + 1], b[k].iloc[:cut + 1])


def test_binary_spread_arithmetic_and_the_25_name_floor():
    idx = pd.date_range("2020-01-31", periods=3, freq="ME")
    cols = [f"S{i}" for i in range(30)]
    S = pd.DataFrame([[1.0] * 15 + [0.0] * 15] * 3, index=idx, columns=cols)
    F = pd.DataFrame([[0.03] * 15 + [0.01] * 15] * 3, index=idx, columns=cols)
    assert abs(dv.binary_spread(S, F) - 0.02) < 1e-12
    assert np.isnan(dv.binary_spread(S.iloc[:, :20], F.iloc[:, :20]))          # 20 names < the IC's 25-name floor -> excluded


def test_avoid_mod_stops_a_seasonal_signal_from_matching_its_own_alignment():
    rng = np.random.default_rng(4)
    T, N = 132, 40
    idx = pd.date_range("2010-01-31", periods=T, freq="ME")
    cols = [f"S{i}" for i in range(N)]
    pattern = rng.normal(size=(12, N))
    S = pd.DataFrame(np.tile(pattern, (T // 12, 1)), index=idx, columns=cols)        # a signal with a strict annual cycle
    F = pd.DataFrame(0.5 * np.tile(pattern, (T // 12, 1)) + rng.normal(size=(T, N)), index=idx, columns=cols)
    ic1, p_plain, *_ = ds.ic_test(S, F, np.random.default_rng(1), draws=2000, min_shift=12, min_rows=60)
    ic2, p_avoid, *_ = ds.ic_test(S, F, np.random.default_rng(1), draws=2000, min_shift=12, min_rows=60, avoid_mod=12)
    assert ic1 == ic2 and p_plain > 0.05 and p_avoid < 0.01        # ~1/12 of plain shifts reproduce the observed alignment


def test_current_incomplete_month_is_dropped_but_a_completed_one_is_kept():
    cal = pd.bdate_range("2020-01-01", "2020-03-18")
    assert len(dv.month_end_positions(cal, today="2020-03-18")) == 2           # March is still forming
    assert len(dv.month_end_positions(cal, today="2020-04-02")) == 3           # by April it is a real (if short) last bar


def test_names_without_history_are_nan_not_non_payers():
    cal, pos = cal_and_pos("2019-01-01", "2021-12-31")
    R = pd.DataFrame({"OLD": 100.0, "NEW": 100.0}, index=cal)
    divs = {"OLD": pd.Series([5.0], index=pd.to_datetime(["2019-06-14"])), "NEW": pd.Series(dtype=float)}
    first = {"OLD": pd.Timestamp("2015-01-01"), "NEW": pd.Timestamp("2020-03-02")}   # NEW listed in March 2020
    s = dv.dividend_signals(R, divs, cal, pos, first)
    dates = list(cal[pos])
    d = dates.index(pd.Timestamp("2020-05-29"))                                # DIV1 needs June 2019 to be observable
    assert s["DIV1"]["OLD"].iloc[d] == 1.0 and np.isnan(s["DIV1"]["NEW"].iloc[d])
    assert np.isnan(s["DYLD"]["NEW"].iloc[d])                                   # < 365 days of history
    d2 = dates.index(pd.Timestamp("2021-06-30"))                                # by mid-2021 NEW has a full year of history
    assert s["DYLD"]["NEW"].iloc[d2] == 0.0 and s["DIV1"]["NEW"].iloc[d2] == 0.0
