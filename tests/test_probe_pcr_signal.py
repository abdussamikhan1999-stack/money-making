import datetime

from probe_pcr_signal import nifty_total_oi, price_on_or_after, rolling_percentile, simulate

D = datetime.date


def test_nifty_total_oi_old_format_sums_only_nifty_options():
    def row(inst, sym, opt, oi):
        return ",".join([inst, sym, "x", "0", opt, "0", "0", "0", "0", "0", "0", "0", str(oi)])
    text = "hdr\n" + "\n".join([row("OPTIDX", "NIFTY", "PE", 30), row("OPTIDX", "NIFTY", "CE", 10),
                                row("OPTIDX", "BANKNIFTY", "PE", 999), row("FUTIDX", "NIFTY", "PE", 999)])
    assert nifty_total_oi(text, "old") == (30, 10)


def test_rolling_percentile_uses_only_trailing_window():
    assert rolling_percentile([1, 2, 3, 0], 3) == [None, None, 1.0, 1 / 3]


def test_simulate_fills_one_day_after_signal_not_same_bar():
    # PCR spikes on d0 (percentile 1.0) then collapses on d1 -> exit signal.
    series = [(D(2020, 1, 6), 1.0), (D(2020, 1, 13), 2.0), (D(2020, 1, 20), 0.0)]
    closes = [(D(2020, 1, 6), 100.0), (D(2020, 1, 7), 110.0), (D(2020, 1, 13), 120.0),
              (D(2020, 1, 14), 130.0), (D(2020, 1, 20), 140.0), (D(2020, 1, 21), 150.0)]
    (t,) = simulate(series, closes, 0.9, 0.5, 2, 8)
    # entry signal on 01-13 (first day window=2 fills) -> fill 01-14 @130; exit signal 01-20 -> fill 01-21 @150
    assert t["entry_date"] == D(2020, 1, 13) and t["exit_date"] == D(2020, 1, 20)
    assert price_on_or_after(closes, D(2020, 1, 14)) == 130.0
    assert t["pnl"] > 0 and abs(t["pnl"] - ((150 / 130 - 1 - 0.004 - 16 / 100_000) * 100_000)) < 1e-6
