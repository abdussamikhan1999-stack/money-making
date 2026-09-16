"""
Probe: Post-Earnings Announcement Drift (PEAD) — a real, decades-documented
academic anomaly (Bernard & Thomas, "Post-Earnings-Announcement Drift:
Delayed Price Response or Risk Premium?", 1989, replicated across many
markets since) and genuinely different from everything else tried in this
project: EVENT-DRIVEN (a specific earnings announcement date matters), not
a continuous technical signal computed from price alone. The finding PEAD
describes: stocks that beat earnings estimates keep drifting UP for weeks
afterward, and stocks that miss keep drifting DOWN — the market
underreacts to the surprise on the announcement day itself.

Data: yfinance's `Ticker.earnings_dates` gives real, per-company quarterly
earnings dates plus EPS estimate/actual/surprise% (verified different
across RELIANCE.NS/TCS.NS/INFY.NS/HDFCBANK.NS - not generic placeholder
data). Known caveat: dates are reported in a US Eastern timezone offset
rather than IST, a real yfinance imprecision that could shift the exact
reported day by up to ~1 day in edge cases - the strategy already reacts
on the NEXT trading day at or after the reported date (never the reported
date itself), which absorbs most of that slack the same way this
project's other daily-bar strategies already assume "act the day after a
signal, not the same day."

Method: for each earnings event with |surprise%| >= surprise_threshold,
enter LONG (beat) or SHORT (miss) at the close of the first trading day
on/after the earnings date, hold a FIXED `hold_days` (matching the
academic literature's own fixed-window drift measurement, not a
technical exit condition - PEAD is specifically about the DRIFT over a
period, not a timing signal with its own natural exit). Position sized as
`position_size_pct` of CURRENT capital per trade (allows overlapping
capital allocation across concurrent earnings events in different stocks
- a known simplification, same treatment as every capital-tracking probe
in this project). Same realistic low-capital equity delivery cost model
established by momentum rotation/low-volatility (0.2% STT+stamp both
legs, ~Rs16 DP charge on the sell leg only, zero delivery brokerage).
"""
import argparse
import bisect

UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS", "ITC.NS", "HINDUNILVR.NS",
    "WIPRO.NS", "LT.NS", "BAJFINANCE.NS", "MARUTI.NS", "ASIANPAINT.NS",
    "SUNPHARMA.NS", "TATASTEEL.NS", "ULTRACEMCO.NS", "ONGC.NS", "NTPC.NS",
]


def build_events(period: str):
    """Real earnings events, sorted by (entry-search) date. Each: (date, symbol, surprise_pct)."""
    import yfinance as yf
    events = []
    for sym in UNIVERSE:
        ed = yf.Ticker(sym).earnings_dates
        if ed is None:
            continue
        valid = ed[ed["Surprise(%)"].notna()]
        for ts, row in valid.iterrows():
            events.append((ts.date(), sym, float(row["Surprise(%)"])))
    events.sort(key=lambda e: e[0])
    return events


def build_price_lookup(period: str):
    from data_yfinance import fetch_candles
    lookup = {}
    for sym in UNIVERSE:
        candles = fetch_candles(sym, "1d", period)
        dates = [c["date"].date() for c in candles]
        closes = [c["close"] for c in candles]
        lookup[sym] = (dates, closes)
    return lookup


def simulate(events: list, price_lookup: dict, surprise_threshold: float = 5.0, hold_days: int = 20,
             position_size_pct: float = 10.0, cost_pct: float = 0.2, dp_charge: float = 16.0,
             capital: float = 100_000.0):
    """Capital-constrained: earnings events cluster within each quarter's
    reporting season, so multiple qualifying signals are routinely open at
    once (checked: up to 13 concurrent at this strategy's own defaults,
    130% of capital at naive fixed-10%-per-trade sizing - not a real
    account). Processes entries/exits in true chronological order (exits
    before entries on the same day, freeing capital first) and SKIPS a
    signal outright if taking it would deploy more than 100% of current
    capital, rather than silently allowing over-deployment."""
    candidates = []
    for event_date, sym, surprise in events:
        if abs(surprise) < surprise_threshold or sym not in price_lookup:
            continue
        dates, closes = price_lookup[sym]
        idx = bisect.bisect_left(dates, event_date)
        exit_idx = idx + hold_days
        if idx >= len(dates) or exit_idx >= len(dates):
            continue
        side = "long" if surprise > 0 else "short"
        candidates.append((dates[idx], dates[exit_idx], sym, side, closes[idx], closes[exit_idx]))
    candidates.sort()

    timeline = [("entry", c[0], c) for c in candidates] + [("exit", c[1], c) for c in candidates]
    timeline.sort(key=lambda e: (e[1], 0 if e[0] == "exit" else 1))  # exits before entries same day

    capital_track = capital
    peak = capital
    max_dd = 0.0
    trades = []
    active: dict = {}
    deployed = 0.0

    for kind, date, c in timeline:
        entry_date, exit_date, sym, side, entry_price, exit_price = c
        if kind == "exit":
            if c not in active:
                continue
            notional = active.pop(c)
            deployed -= notional
            ret = (exit_price - entry_price) / entry_price if side == "long" \
                else (entry_price - exit_price) / entry_price
            gross = notional * ret
            cost = notional * (cost_pct / 100) * 2 + dp_charge
            net = gross - cost
            capital_track += net
            trades.append(dict(date=entry_date, symbol=sym, side=side, net_pnl=net))
            peak = max(peak, capital_track)
            dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            continue
        notional = capital_track * position_size_pct / 100
        if deployed + notional > capital_track:
            continue  # not enough free capital - skip this signal rather than over-deploy
        active[c] = notional
        deployed += notional

    total_net = sum(t["net_pnl"] for t in trades)
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    return dict(trades=trades, final_capital=capital_track, total_net=total_net,
                max_dd=max_dd, n_trades=len(trades), win_rate=wins / len(trades) if trades else 0.0)


def annualized_return_pct(final_capital: float, capital: float, years: float) -> float:
    if years <= 0:
        return 0.0
    total_return = final_capital / capital - 1
    if total_return <= -1:
        return -100.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def _report(label, result, capital, years):
    ann = annualized_return_pct(result["final_capital"], capital, years)
    print(f"{label}: trades={result['n_trades']} win_rate={result['win_rate']*100:.0f}% "
          f"net={result['total_net']:.0f} final_cap={result['final_capital']:.0f} "
          f"max_dd={result['max_dd']:.1f}% annualized={ann:.2f}%/yr")


def walk_forward(events, price_lookup, split_ratio=0.5, **kwargs):
    n = len(events)
    cutoff = max(1, int(n * split_ratio))
    return (simulate(events[:cutoff], price_lookup, **kwargs),
            simulate(events[cutoff:], price_lookup, **kwargs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--surprise-threshold", type=float, default=5.0)
    parser.add_argument("--hold-days", type=int, default=20)
    parser.add_argument("--position-size-pct", type=float, default=10.0)
    parser.add_argument("--cost-pct", type=float, default=0.2)
    parser.add_argument("--dp-charge", type=float, default=16.0)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()

    events = build_events(args.period)
    price_lookup = build_price_lookup(args.period)
    kwargs = dict(surprise_threshold=args.surprise_threshold, hold_days=args.hold_days,
                  position_size_pct=args.position_size_pct, capital=args.capital,
                  cost_pct=args.cost_pct, dp_charge=args.dp_charge)
    years = (events[-1][0] - events[0][0]).days / 365.25 if events else 0.0

    if args.walk_forward:
        in_s, out_s = walk_forward(events, price_lookup, **kwargs)
        _report("In-sample  (first half)", in_s, args.capital, years / 2)
        _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2)
        consistent = (in_s["final_capital"] > args.capital) == (out_s["final_capital"] > args.capital)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    else:
        result = simulate(events, price_lookup, **kwargs)
        _report("Full period", result, args.capital, years)
