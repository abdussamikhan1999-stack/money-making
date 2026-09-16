"""
Probe: Low-Volatility Anomaly (the "Betting Against Beta" family) — a
genuinely different RISK-BASED cross-sectional factor, not a timing/
technical signal like almost everything else in this project. Sourced from
real, decades-of-data academic literature: Frazzini & Pedersen's "Betting
Against Beta" (2014) and Ang/Hodrick/Xing/Zhang's "The Cross-Section of
Volatility and Expected Returns" (2006) — the well-documented finding that
low-volatility/low-beta stocks earn HIGHER risk-adjusted returns than
high-volatility ones, the opposite of CAPM's prediction. Standard
explanation: leverage-constrained investors (can't or won't use margin)
bid up high-beta stocks chasing extra return without leverage, structurally
overpricing them and underpricing the low-beta/low-vol end.

Genuinely different from momentum rotation (CLAUDE.md's "Fourth", which
decayed to negative in the most recent quarter): ranks stocks by trailing
VOLATILITY (a risk measure), not trailing RETURN (a momentum/price-
extrapolation measure) — a different theoretical story (risk-based
mispricing from leverage constraints), not behavioral momentum.

Method (same monthly-rebalance architecture and cost model as momentum
rotation, for direct comparability): rank a fixed NSE large/mid-cap
universe by trailing `lookback_days` daily-return volatility (stdev),
equal-weight the bottom `top_n` (least volatile) stocks, rebalance
monthly. Same realistic low-capital equity delivery cost model already
established in this project: ~0.2% STT+stamp on both legs, a flat ~₹16
DP charge on the SELL leg only (Zerodha equity delivery brokerage is
zero) — costs applied only on stocks entering/leaving the portfolio at
each rebalance (holding a stock across periods pays nothing extra), the
same simplification momentum rotation's own probe used.

Same survivorship-bias caveat as momentum rotation's own writeup: this
universe is hand-picked using TODAY's well-known large/mid-caps, not a
point-in-time historical constituent list — a real risk on top of
whatever this backtest finds, not resolved here for lack of a free
point-in-time source.
"""
import argparse
import statistics

UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS", "ITC.NS", "HINDUNILVR.NS",
    "WIPRO.NS", "LT.NS", "BAJFINANCE.NS", "MARUTI.NS", "ASIANPAINT.NS",
    "SUNPHARMA.NS", "TATASTEEL.NS", "ULTRACEMCO.NS", "ONGC.NS", "NTPC.NS",
]


def build_price_panel(period: str):
    from data_yfinance import fetch_candles
    per_symbol = {}
    for sym in UNIVERSE:
        candles = fetch_candles(sym, "1d", period)
        per_symbol[sym] = {c["date"].date(): c["close"] for c in candles}
    common_dates = set.intersection(*(set(d.keys()) for d in per_symbol.values()))
    dates = sorted(common_dates)
    panel = {sym: [per_symbol[sym][d] for d in dates] for sym in UNIVERSE}
    return dates, panel


def _volatility(prices: list[float]) -> float | None:
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets)


def simulate(dates: list, panel: dict, lookback_days: int = 252, top_n: int = 5,
             capital: float = 100_000.0, cost_pct: float = 0.2, dp_charge: float = 16.0):
    rebalance_idxs = [i for i in range(1, len(dates)) if dates[i].month != dates[i - 1].month]
    rebalance_idxs = [i for i in rebalance_idxs if i >= lookback_days]
    if not rebalance_idxs:
        return dict(final_capital=capital, max_dd=0.0, n_rebalances=0, history=[])

    capital_track = capital
    peak = capital
    max_dd = 0.0
    current_holdings: set[str] = set()
    history = []

    for k, i in enumerate(rebalance_idxs):
        vols = {}
        for sym in panel:
            v = _volatility(panel[sym][i - lookback_days:i])
            if v is not None:
                vols[sym] = v
        ranked = sorted(vols, key=vols.get)
        new_holdings = set(ranked[:top_n])

        entries = new_holdings - current_holdings
        exits = current_holdings - new_holdings
        per_slot = 1.0 / top_n if top_n else 0.0
        turnover_cost_pct = (len(entries) + len(exits)) * per_slot * (cost_pct / 100)
        capital_track -= capital_track * turnover_cost_pct
        capital_track -= len(exits) * dp_charge
        current_holdings = new_holdings

        end_i = rebalance_idxs[k + 1] if k + 1 < len(rebalance_idxs) else len(dates) - 1
        period_returns = [(panel[sym][end_i] - panel[sym][i]) / panel[sym][i] for sym in new_holdings]
        if period_returns:
            capital_track *= (1 + sum(period_returns) / len(period_returns))

        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        history.append(dict(date=dates[i], capital=capital_track, holdings=sorted(new_holdings)))

    return dict(final_capital=capital_track, max_dd=max_dd, n_rebalances=len(rebalance_idxs), history=history)


def benchmark_return_pct(dates: list, panel: dict, start_i: int, end_i: int) -> float:
    """Equal-weight buy-and-hold of the whole universe over the same window, for context."""
    rets = [(panel[sym][end_i] - panel[sym][start_i]) / panel[sym][start_i] for sym in panel]
    return sum(rets) / len(rets) * 100


def annualized_return_pct(final_capital: float, capital: float, years: float) -> float:
    if years <= 0:
        return 0.0
    total_return = final_capital / capital - 1
    if total_return <= -1:
        return -100.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def _report(label, result, capital, years, dates=None, panel=None, start_i=None, end_i=None):
    ann = annualized_return_pct(result["final_capital"], capital, years)
    bench = ""
    if dates is not None and start_i is not None:
        bench_total = benchmark_return_pct(dates, panel, start_i, end_i)
        bench = f" bench_total={bench_total:.1f}%"
    print(f"{label}: rebalances={result['n_rebalances']} final_cap={result['final_capital']:.0f} "
          f"max_dd={result['max_dd']:.1f}% annualized={ann:.2f}%/yr{bench}")


def walk_forward(dates, panel, split_ratio=0.5, **kwargs):
    n = len(dates)
    cutoff = max(1, int(n * split_ratio))
    return (simulate(dates[:cutoff], {s: p[:cutoff] for s, p in panel.items()}, **kwargs),
            simulate(dates[cutoff:], {s: p[cutoff:] for s, p in panel.items()}, **kwargs))


def quarter_split(dates, panel, **kwargs):
    n = len(dates)
    chunk = max(1, n // 4)
    chunks = []
    for i in range(0, n, chunk):
        d = dates[i:i + chunk]
        p = {s: prices[i:i + chunk] for s, prices in panel.items()}
        chunks.append((d, p))
    return [simulate(d, p, **kwargs) for d, p in chunks[:4]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--lookback-days", type=int, default=252)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--cost-pct", type=float, default=0.2)
    parser.add_argument("--dp-charge", type=float, default=16.0)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    dates, panel = build_price_panel(args.period)
    kwargs = dict(lookback_days=args.lookback_days, top_n=args.top_n, capital=args.capital,
                  cost_pct=args.cost_pct, dp_charge=args.dp_charge)
    years = (dates[-1] - dates[0]).days / 365.25

    if args.walk_forward:
        in_s, out_s = walk_forward(dates, panel, **kwargs)
        cutoff = max(1, int(len(dates) * 0.5))
        _report("In-sample  (first half)", in_s, args.capital, years / 2, dates, panel, 0, cutoff - 1)
        _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2, dates, panel, cutoff, len(dates) - 1)
        consistent = (in_s["final_capital"] > args.capital) == (out_s["final_capital"] > args.capital)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    elif args.quarter_split:
        chunks = quarter_split(dates, panel, **kwargs)
        for i, c in enumerate(chunks):
            _report(f"Q{i+1}", c, args.capital, years / 4)
    else:
        result = simulate(dates, panel, **kwargs)
        _report("Full period", result, args.capital, years, dates, panel, 0, len(dates) - 1)
