import datetime

from probe_futures_basis_signal import (
    nifty_near_month_futures_close,
    price_on_or_before,
    simulate,
)

D = datetime.date


def test_near_month_futures_close_old_format_picks_nearest_expiry():
    def row(inst, sym, expiry, close):
        return ",".join([inst, sym, expiry, "0", "XX", "0", "0", "0", str(close), "0"])

    text = "hdr\n" + "\n".join(
        [
            row("FUTIDX", "NIFTY", "25-Feb-2026", 100.5),
            row("FUTIDX", "NIFTY", "25-Jan-2026", 99.0),  # nearer expiry -> near-month
            row("FUTIDX", "BANKNIFTY", "25-Jan-2026", 999.0),
            row("OPTIDX", "NIFTY", "25-Jan-2026", 1.0),
        ]
    )
    assert nifty_near_month_futures_close(text, "old") == 99.0


def test_near_month_futures_close_new_format_uses_tckr_and_fininstrmtp():
    header = "TckrSymb,FinInstrmTp,XpryDt,ClsPric,Other"
    rows = [
        "NIFTY,IDF,2026-02-25,101.0,x",
        "NIFTY,IDF,2026-01-28,100.0,x",  # nearer expiry
        "NIFTY,IDO,2026-01-28,1.0,x",  # option, must be excluded
        "BANKNIFTY,IDF,2026-01-28,999.0,x",  # wrong symbol, must be excluded
    ]
    text = header + "\n" + "\n".join(rows)
    assert nifty_near_month_futures_close(text, "new") == 100.0


def test_price_on_or_before_never_looks_forward():
    closes = [(D(2020, 1, 6), 100.0), (D(2020, 1, 10), 110.0), (D(2020, 1, 15), 120.0)]
    assert price_on_or_before(closes, D(2020, 1, 12)) == 110.0
    assert price_on_or_before(closes, D(2020, 1, 6)) == 100.0
    assert price_on_or_before(closes, D(2020, 1, 1)) is None


def test_simulate_fills_one_day_after_signal_not_same_bar():
    series = [(D(2020, 1, 6), 1.0), (D(2020, 1, 13), 2.0), (D(2020, 1, 20), 0.0)]
    closes = [(D(2020, 1, 6), 100.0), (D(2020, 1, 7), 110.0), (D(2020, 1, 13), 120.0),
              (D(2020, 1, 14), 130.0), (D(2020, 1, 20), 140.0), (D(2020, 1, 21), 150.0)]
    (t,) = simulate(series, closes, 0.9, 0.5, 2, 8, direction="high")
    assert t["entry_date"] == D(2020, 1, 13) and t["exit_date"] == D(2020, 1, 20)
    assert t["pnl"] > 0


def test_direction_low_mirrors_the_percentile_not_the_entry_logic():
    # window=3: the trailing-window MAXIMUM always has raw percentile 1.0 (every entry in an
    # increasing series), the MINIMUM always has raw percentile 1/3 -> mirrored (low) 2/3.
    dates = [D(2020, 1, d) for d in (6, 13, 20, 27)] + [D(2020, 2, 3)]
    closes = [(d, 100.0) for d in dates] + [
        (d + datetime.timedelta(days=1), 100.0) for d in dates
    ]
    increasing = list(zip(dates, [1.0, 2.0, 3.0, 4.0, 5.0]))
    decreasing = list(zip(dates, [5.0, 4.0, 3.0, 2.0, 1.0]))

    # "high" (momentum) hypothesis: rides an increasing series, ignores a decreasing one.
    assert len(simulate(increasing, closes, 0.60, -1.0, 3, 1, direction="high")) == 1
    assert len(simulate(decreasing, closes, 0.60, -1.0, 3, 1, direction="high")) == 0

    # "low" (contrarian) hypothesis mirrors the percentile: the opposite pattern.
    assert len(simulate(increasing, closes, 0.60, -1.0, 3, 1, direction="low")) == 0
    assert len(simulate(decreasing, closes, 0.60, -1.0, 3, 1, direction="low")) == 1
