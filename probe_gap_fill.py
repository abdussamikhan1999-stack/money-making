"""
Probe: intraday gap-fill mean reversion — a genuinely different mechanism
from everything in CLAUDE.md so far (not trend, not multi-day mean
reversion, not a spread, not calendar/theta). Rule, sourced from widely
repeated (if loosely quantified) trading-forum/blog claims about NSE
gap behavior: when today's open gaps away from yesterday's close by more
than a threshold, bet on reversion back toward yesterday's close within
the same session.

  gap_pct = (open - prior_close) / prior_close * 100
  gap up  (gap_pct > +threshold):  SHORT at open, target = prior_close
  gap down (gap_pct < -threshold): LONG  at open, target = prior_close
  stop = open +/- stop_extra_pct (a fixed fraction beyond the open, i.e.
  betting the gap extends no further than this before reverting)
  exit at target, stop, or end-of-day close — whichever the day's OHLC
  path hits first. Since real intraday tick path isn't available from
  daily bars, this uses the SAME conservative approximation as
  backtest.py/backtest_daily.py: check the adverse extreme (stop side)
  before the favorable one, so a day that could satisfy both isn't
  scored as a lucky win.

Same-day entry+exit means this can only be traded via an intraday
(MIS/futures) product, not equity delivery — modeled with a flat
`commission_pct` round-trip cost meant to approximate NSE intraday equity
STT (0.025%, sell-side) + exchange/SEBI charges + GST + a discount broker's
intraday brokerage, all as a single all-in percentage of notional, in the
same simplified spirit as this project's other flat commission-per-trade
modeling (see CLAUDE.md's transaction-cost section) — not a claim of
precision, just a realistic order of magnitude.

Not ported into daily_strategy.py's push/check_entry/check_exit interface:
that interface only sees the day's CLOSE, but this strategy's decision and
entry price both depend on the day's OPEN relative to yesterday's close, so
it needs its own loop (same reasoning as the momentum-rotation and
pairs-trading probes already documented in CLAUDE.md).
"""
import argparse
import statistics


def simulate_gap_fill(daily: list[dict], gap_threshold_pct: float = 0.5, stop_extra_pct: float = 0.5,
                       capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
                       commission_pct: float = 0.05, max_drawdown_pct: float = 20.0):
    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []

    for i in range(1, len(daily)):
        if halted:
            break
        prior_close = daily[i - 1]["close"]
        bar = daily[i]
        gap_pct = (bar["open"] - prior_close) / prior_close * 100

        if gap_pct > gap_threshold_pct:
            side = "short"
        elif gap_pct < -gap_threshold_pct:
            side = "long"
        else:
            continue

        entry = bar["open"]
        target = prior_close
        if side == "short":
            stop = entry * (1 + stop_extra_pct / 100)
        else:
            stop = entry * (1 - stop_extra_pct / 100)

        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            continue
        risk_amount = capital_track * risk_per_trade_pct / 100
        qty = risk_amount / risk_per_unit
        if qty <= 0:
            continue

        if side == "short":
            hit_stop = bar["high"] >= stop
            hit_target = bar["low"] <= target
        else:
            hit_stop = bar["low"] <= stop
            hit_target = bar["high"] >= target

        if hit_stop:
            exit_price, outcome = stop, "stop"
        elif hit_target:
            exit_price, outcome = target, "target"
        else:
            exit_price, outcome = bar["close"], "eod"

        gross_pnl = (entry - exit_price) * qty if side == "short" else (exit_price - entry) * qty
        cost = entry * qty * commission_pct / 100
        net_pnl = gross_pnl - cost

        capital_track += net_pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        if max_dd >= max_drawdown_pct:
            halted = True

        trades.append(dict(date=bar["date"], side=side, gap_pct=gap_pct, outcome=outcome,
                            gross_pnl=gross_pnl, net_pnl=net_pnl))

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    return dict(trades=trades, final_capital=capital_track, total_gross=total_gross,
                total_net=total_net, max_dd=max_dd, halted=halted, n_trades=len(trades))


def annualized_return_pct(result, capital, years):
    if years <= 0:
        return 0.0
    total_return = result["total_net"] / capital
    if total_return <= -1:
        return -100.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def _report(label, result, capital, years):
    r = result
    ann = annualized_return_pct(r, capital, years)
    print(f"{label}: trades={r['n_trades']} gross={r['total_gross']:.0f} net={r['total_net']:.0f} "
          f"final_cap={r['final_capital']:.0f} max_dd={r['max_dd']:.1f}% "
          f"annualized={ann:.2f}%/yr halted={r['halted']}")


def walk_forward(daily, split_ratio=0.5, **kwargs):
    n = len(daily)
    cutoff = max(1, int(n * split_ratio))
    before, after = daily[:cutoff], daily[cutoff:]
    return simulate_gap_fill(before, **kwargs), simulate_gap_fill(after, **kwargs)


def quarter_split(daily, **kwargs):
    n = len(daily)
    chunk = max(1, n // 4)
    chunks = [daily[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate_gap_fill(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--gap-threshold-pct", type=float, default=0.5)
    parser.add_argument("--stop-extra-pct", type=float, default=0.5)
    parser.add_argument("--commission-pct", type=float, default=0.05)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    daily = fetch_daily_yfinance(args.symbol, args.period)
    kwargs = dict(gap_threshold_pct=args.gap_threshold_pct, stop_extra_pct=args.stop_extra_pct,
                  capital=args.capital, risk_per_trade_pct=args.risk_per_trade_pct,
                  commission_pct=args.commission_pct)
    span_days = (daily[-1]["date"] - daily[0]["date"]).days
    years = span_days / 365.25

    if args.walk_forward:
        in_s, out_s = walk_forward(daily, **kwargs)
        _report("In-sample  (first half)", in_s, args.capital, years / 2)
        _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2)
        consistent = (in_s["total_net"] > 0) == (out_s["total_net"] > 0)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    elif args.quarter_split:
        chunks = quarter_split(daily, **kwargs)
        for i, c in enumerate(chunks):
            _report(f"Q{i+1}", c, args.capital, years / 4)
    else:
        result = simulate_gap_fill(daily, **kwargs)
        _report("Full period", result, args.capital, years)
        gaps = [t["gap_pct"] for t in result["trades"]]
        if gaps:
            print(f"gap_pct: mean={statistics.mean(map(abs, gaps)):.2f} n={len(gaps)}")
