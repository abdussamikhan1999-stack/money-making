"""Forward paper-tracking for the Thirty-eighth/Thirty-ninth entries' IBS
rotation finding: the strongest, most rigorously corroborated result in
this project's history (see CLAUDE.md), but every entry has flagged the
same remaining gap — no backtest substitutes for genuine out-of-sample
data that didn't exist when the backtest was written.

Run this ONCE A MONTH (matches the strategy's own rebalance cadence).
(Sixty-fourth entry: run on the LAST FEW TRADING DAYS of the calendar month --
the backtest's edge is concentrated in month-end entries; the first record,
2026-09-17, was mid-month, the backtest's worst phase.)
Each run does two things:
  1. Marks the PREVIOUS run's picks to market using today's prices,
     appending a realized-P&L record to the log (using this project's
     standard equity cost model, same as probe_ibs_rotation.simulate()).
  2. Ranks WIDE_UNIVERSE by trailing 5-day avg IBS (the Thirty-ninth
     entry's recommended live rule: top_k=5, lookback=5, monthly
     rebalance, 52-stock universe) and logs today's 5 picks as the new
     open position.

The log (`paper_track_ibs_rotation_log.json`) is an append-only forward
record. DO NOT edit, reset, or reinterpret past entries retroactively —
the entire point is an untouched, dated record this project's own
backtests cannot fabricate. Reuses rank_by_ibs()/dates_closes_maps() from
probe_ibs_rotation.py so this can never silently drift from what was
actually validated (see test_paper_track_ibs_rotation.py).
"""
import json
import os
from datetime import date

from probe_ibs_rotation import dates_closes_maps, rank_by_ibs, fetch_calendar, latest_settled_date
from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series

LOG_PATH = os.path.join(os.path.dirname(__file__), "paper_track_ibs_rotation_log.json")
TOP_K = 5
LOOKBACK = 5
CAPITAL = 100_000.0
COST_PCT = 0.2
DP_CHARGE = 16.0


def load_log() -> list[dict]:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(log: list[dict]) -> None:
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, default=str)


def mark_to_market(record: dict, dates_map: dict, closes_map: dict, as_of) -> dict:
    """Close out a still-open record using today's prices, same cost model
    as probe_ibs_rotation.simulate()."""
    from probe_ibs_rotation import price_at_or_before
    notional_each = CAPITAL / len(record["picks"])
    net_pnl = 0.0
    exit_prices = {}
    for pick in record["picks"]:
        sym = pick["symbol"]
        exit_px = price_at_or_before(dates_map[sym], closes_map[sym], as_of)
        if exit_px is None:
            continue
        exit_prices[sym] = exit_px
        ret = (exit_px - pick["entry_price"]) / pick["entry_price"]
        gross = notional_each * ret
        cost = notional_each * (COST_PCT / 100) * 2 + DP_CHARGE
        net_pnl += gross - cost
    record["exit"] = {
        "date": str(as_of),
        "prices": exit_prices,
        "net_pnl": round(net_pnl, 2),
        "net_pnl_pct": round(net_pnl / CAPITAL * 100, 3),
    }
    return record


def run() -> None:
    log = load_log()
    series = build_wide_price_series("1y")
    dates_map, closes_map = dates_closes_maps(series)
    as_of = latest_settled_date(fetch_calendar("1y"))
    if as_of is None:
        print("No settled trading day available yet, aborting.")
        return
    today_str = str(as_of)

    if log and log[-1]["exit"] is None:
        mark_to_market(log[-1], dates_map, closes_map, as_of)
        print(f"Marked to market: {log[-1]['date']} -> {today_str}, "
              f"net_pnl={log[-1]['exit']['net_pnl']:.0f} "
              f"({log[-1]['exit']['net_pnl_pct']:+.2f}%)")

    if log and log[-1]["date"] == today_str:
        print(f"Already logged for {today_str}, skipping new pick.")
        save_log(log)
        return

    picks = rank_by_ibs(series, dates_map, closes_map, as_of, LOOKBACK, TOP_K)
    record = {
        "date": today_str,
        "picks": [{"symbol": sym, "entry_price": px, "entry_ibs": round(score, 4)}
                   for score, sym, px in picks],
        "exit": None,
    }
    log.append(record)
    save_log(log)

    print(f"Logged {len(picks)} picks for {today_str}:")
    for score, sym, px in picks:
        print(f"  {sym}: entry_ibs={score:.4f} entry_price={px:.2f}")
    print(f"Log: {LOG_PATH}")


if __name__ == "__main__":
    run()
