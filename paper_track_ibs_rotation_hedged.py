"""Forward paper-tracking for the Forty-sixth/Forty-seventh entries' hedged
IBS rotation: the same monthly long-only stock picks as
paper_track_ibs_rotation.py (entry 41), PLUS a short NIFTYBEES.NS leg sized
to 0.5x the strategy's regression beta -- the drawdown-minimizing point
inside the Forty-seventh entry's validated 0.375-0.625 robust range.

Run this ONCE A MONTH, same cadence as paper_track_ibs_rotation.py. This is
a SEPARATE, parallel forward record -- it does not touch or replace that
script's log. Both trackers should be checked/extended monthly going
forward; they represent this project's two live candidates (unhedged and
half-hedged), distinct from the large negative-result history in CLAUDE.md's
numbered entries.

The log (`paper_track_ibs_rotation_hedged_log.json`) is append-only. DO NOT
edit, reset, or reinterpret past entries retroactively.
"""
import json
import os
from datetime import date

from probe_ibs_rotation import (
    dates_closes_maps, rank_by_ibs, fetch_calendar, price_at_or_before, month_end_dates,
    latest_settled_date,
)
from probe_ibs_rotation_widen import WIDE_UNIVERSE, build_wide_price_series  # noqa: F401
from probe_ibs_rotation_longshort import long_only_monthly_returns, nifty_monthly_returns
from probe_ibs_rotation_hedged import compute_beta
from probe_ibs_rotation_etf_hedge import ETF_SYMBOL, ETF_COST_PCT, fetch_etf_series
from paper_track_ibs_rotation import TOP_K, LOOKBACK, CAPITAL, COST_PCT, DP_CHARGE

LOG_PATH = os.path.join(os.path.dirname(__file__), "paper_track_ibs_rotation_hedged_log.json")
HEDGE_RATIO = 0.5


def load_log() -> list[dict]:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(log: list[dict]) -> None:
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, default=str)


def compute_live_beta() -> float:
    """Same regression this project's backtests use (probe_ibs_rotation_hedged
    .compute_beta), recomputed on the full 10y window each run -- beta drifts
    slowly, but this stays honest rather than hardcoding the 1.143 figure
    the Forty-fifth/Forty-sixth/Forty-seventh entries measured on past data."""
    series = build_wide_price_series("10y")
    calendar_candles = fetch_calendar("10y")
    rebalance_dates = month_end_dates(calendar_candles)
    lo_rets = long_only_monthly_returns(rebalance_dates, series, TOP_K, LOOKBACK, COST_PCT, DP_CHARGE, CAPITAL)
    nifty_rets = nifty_monthly_returns(rebalance_dates, calendar_candles)
    return compute_beta(lo_rets, nifty_rets)


def mark_to_market(record: dict, dates_map: dict, closes_map: dict,
                    etf_dates: list, etf_closes: list, as_of) -> dict:
    """Close out a still-open record (stock legs + ETF short leg) using
    today's prices, same cost model as simulate_etf_hedged()."""
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

    etf = record["etf_hedge"]
    etf_exit_px = price_at_or_before(etf_dates, etf_closes, as_of)
    etf_pnl = 0.0
    if etf["qty"] > 0 and etf_exit_px is not None:
        etf_pnl = -etf["qty"] * (etf_exit_px - etf["entry_price"])
        etf_pnl -= etf["qty"] * etf["entry_price"] * (ETF_COST_PCT / 100) * 2

    total_pnl = net_pnl + etf_pnl
    record["exit"] = {
        "date": str(as_of),
        "stock_exit_prices": exit_prices,
        "etf_exit_price": etf_exit_px,
        "stock_net_pnl": round(net_pnl, 2),
        "etf_pnl": round(etf_pnl, 2),
        "total_net_pnl": round(total_pnl, 2),
        "total_net_pnl_pct": round(total_pnl / CAPITAL * 100, 3),
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

    etf_candles = fetch_etf_series("1y")
    etf_dates = [c["date"].date() for c in etf_candles]
    etf_closes = [c["close"] for c in etf_candles]

    if log and log[-1]["exit"] is None:
        mark_to_market(log[-1], dates_map, closes_map, etf_dates, etf_closes, as_of)
        exit_info = log[-1]["exit"]
        print(f"Marked to market: {log[-1]['date']} -> {today_str}, "
              f"total_net_pnl={exit_info['total_net_pnl']:.0f} "
              f"({exit_info['total_net_pnl_pct']:+.2f}%)")

    if log and log[-1]["date"] == today_str:
        print(f"Already logged for {today_str}, skipping new pick.")
        save_log(log)
        return

    beta = compute_live_beta()
    picks = rank_by_ibs(series, dates_map, closes_map, as_of, LOOKBACK, TOP_K)

    etf_p0 = price_at_or_before(etf_dates, etf_closes, as_of)
    hedge_notional = HEDGE_RATIO * beta * CAPITAL
    import math
    etf_qty = math.floor(hedge_notional / etf_p0) if etf_p0 else 0

    record = {
        "date": today_str,
        "beta": round(beta, 4),
        "hedge_ratio": HEDGE_RATIO,
        "picks": [{"symbol": sym, "entry_price": px, "entry_ibs": round(score, 4)}
                   for score, sym, px in picks],
        "etf_hedge": {"symbol": ETF_SYMBOL, "qty": etf_qty, "entry_price": etf_p0},
        "exit": None,
    }
    log.append(record)
    save_log(log)

    print(f"beta={beta:.3f}, hedge_ratio={HEDGE_RATIO}, hedge_notional=Rs {hedge_notional:,.0f}")
    print(f"Logged {len(picks)} long picks for {today_str}:")
    for score, sym, px in picks:
        print(f"  {sym}: entry_ibs={score:.4f} entry_price={px:.2f}")
    print(f"SHORT {ETF_SYMBOL}: {etf_qty} shares @ {etf_p0:.2f}")
    print(f"Log: {LOG_PATH}")


if __name__ == "__main__":
    run()
