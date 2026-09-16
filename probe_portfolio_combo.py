"""
Probe: portfolio combination of this project's own already-tested "real
but thin" survivors, run SIMULTANEOUSLY. Not a new signal - every prior
entry in this project evaluated one mechanism on one instrument at a
time; none tested the standard, sound portfolio-construction principle
that several small, uncorrelated real edges can combine into a better
risk-adjusted return than any single one alone (this is literally how
real quant funds combine many small edges - diversification across
UNCORRELATED return streams, not stacking multiple signals into one
trade, which this project's Eleventh/Twenty-second entries already found
hurts).

Components chosen from this project's own established "real, clears
walk-forward/quarter-split/perturbation, just too thin alone" survivors,
each at its own best-known instrument and default parameters:
  - RSI-2 on ^NSEI (Third entry - the most theoretically coherent result)
  - 3-bar breakout on SBIN.NS and HDFCBANK.NS (Twelfth entry - the two
    strongest non-anomalous survivors, sizing genuinely helps)
  - MACD on TCS.NS (Twenty-first entry)
  - Turtle Soup on AXISBANK.NS (Twentieth entry - smoothest perturbation
    sweep in the whole project)

Each component gets its own equal slice of total capital and its own
independent RiskManager/PaperBroker (reuses backtest_daily.py's
simulate_daily() unmodified, via its dated_trades side-channel - see that
function's own docstring). Merging the dated trades from every component
into one chronological list, starting from TOTAL capital, gives a single
combined equity curve - this is what a real account running all five
positions at once would actually see, including genuine correlation
effects (a bad week for Indian equities broadly could hit several
components at once, which no single-component backtest could reveal).
"""
import argparse

from backtest_daily import simulate_daily, fetch_daily_yfinance
from daily_strategy import ConnorsRSI2Strategy, ThreeBarBreakoutStrategy, MACDStrategy, TurtleSoupStrategy

COMPONENTS = [
    # RSI-2 on ^NSEI (its own most-cited instrument) needs a stop distance
    # scaled to index POINTS (often hundreds), which a small per-component
    # capital slice can't afford even one unit of - not a real tradable
    # unit anyway (Nifty spot isn't directly buyable; only futures/options
    # are, both fixed-lot). Used RELIANCE.NS instead, one of RSI-2's other
    # 8 real Third-entry survivors and priced in ordinary per-share terms.
    ("rsi2/RELIANCE.NS", ConnorsRSI2Strategy, "RELIANCE.NS", {}),
    ("threebar/SBIN.NS", ThreeBarBreakoutStrategy, "SBIN.NS", {}),
    ("threebar/HDFCBANK.NS", ThreeBarBreakoutStrategy, "HDFCBANK.NS", {}),
    ("macd/TCS.NS", MACDStrategy, "TCS.NS", {}),
    ("turtlesoup/AXISBANK.NS", TurtleSoupStrategy, "AXISBANK.NS", {}),
]


def run_portfolio(period: str = "10y", capital: float = 100_000.0, risk_per_trade_pct: float = 1.0,
                   commission_per_trade: float = 20.0, components=None):
    components = components or COMPONENTS
    slice_capital = capital / len(components)
    dated_trades: list[tuple] = []
    per_component_final = {}

    for label, strategy_cls, symbol, kwargs in components:
        daily = fetch_daily_yfinance(symbol, period)
        component_trades: list[tuple] = []
        broker, risk = simulate_daily(daily, strategy_cls(**kwargs), capital=slice_capital,
                                       risk_per_trade_pct=risk_per_trade_pct,
                                       commission_per_trade=commission_per_trade,
                                       dated_trades=component_trades)
        dated_trades.extend((date, label, pnl) for date, pnl in component_trades)
        per_component_final[label] = dict(final_capital=slice_capital + broker.cash_pnl,
                                           n_trades=len(broker.trade_log))

    dated_trades.sort(key=lambda t: t[0])
    capital_track = capital
    peak = capital
    max_dd = 0.0
    curve = []
    for date, label, pnl in dated_trades:
        capital_track += pnl
        peak = max(peak, capital_track)
        dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        curve.append((date, capital_track))

    return dict(final_capital=capital_track, max_dd=max_dd, n_trades=len(dated_trades),
                per_component=per_component_final, curve=curve)


def annualized_return_pct(final_capital: float, capital: float, years: float) -> float:
    if years <= 0:
        return 0.0
    total_return = final_capital / capital - 1
    if total_return <= -1:
        return -100.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def walk_forward(period: str = "10y", split_ratio: float = 0.5, **kwargs):
    """Splits by DATE (not by trade count) using each component's own
    fetched daily bars, same convention as backtest_daily.py's own
    walk_forward_daily()."""
    components = kwargs.pop("components", None) or COMPONENTS
    capital = kwargs.get("capital", 100_000.0)
    halves = ([], [])
    for label, strategy_cls, symbol, skwargs in components:
        daily = fetch_daily_yfinance(symbol, period)
        cutoff = max(1, int(len(daily) * split_ratio))
        halves[0].append((label, daily[:cutoff]))
        halves[1].append((label, daily[cutoff:]))

    results = []
    for half_data in halves:
        slice_capital = capital / len(components)
        dated_trades = []
        for (label, strategy_cls, symbol, skwargs), (_, daily) in zip(components, half_data):
            component_trades = []
            simulate_daily(daily, strategy_cls(**skwargs), capital=slice_capital,
                            risk_per_trade_pct=kwargs.get("risk_per_trade_pct", 1.0),
                            commission_per_trade=kwargs.get("commission_per_trade", 20.0),
                            dated_trades=component_trades)
            dated_trades.extend((date, pnl) for date, pnl in component_trades)
        dated_trades.sort(key=lambda t: t[0])
        capital_track = capital
        peak = capital
        max_dd = 0.0
        for date, pnl in dated_trades:
            capital_track += pnl
            peak = max(peak, capital_track)
            dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        results.append(dict(final_capital=capital_track, max_dd=max_dd, n_trades=len(dated_trades)))
    return results[0], results[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=1.0)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()

    if args.walk_forward:
        in_s, out_s = walk_forward(period=args.period, capital=args.capital,
                                    risk_per_trade_pct=args.risk_per_trade_pct,
                                    commission_per_trade=args.commission_per_trade)
        for label, r in (("In-sample  (first half)", in_s), ("Out-of-sample (2nd half)", out_s)):
            ann = annualized_return_pct(r["final_capital"], args.capital, 5.0)
            print(f"{label}: trades={r['n_trades']} final_cap={r['final_capital']:.0f} "
                  f"max_dd={r['max_dd']:.1f}% annualized~{ann:.2f}%/yr")
        consistent = (in_s["final_capital"] > args.capital) == (out_s["final_capital"] > args.capital)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    else:
        result = run_portfolio(period=args.period, capital=args.capital,
                                risk_per_trade_pct=args.risk_per_trade_pct,
                                commission_per_trade=args.commission_per_trade)
        years = (result["curve"][-1][0] - result["curve"][0][0]).days / 365.25 if result["curve"] else 0.0
        ann = annualized_return_pct(result["final_capital"], args.capital, years)
        print(f"Combined portfolio: trades={result['n_trades']} final_cap={result['final_capital']:.0f} "
              f"max_dd={result['max_dd']:.1f}% annualized={ann:.2f}%/yr")
        print("\nPer-component (own capital slice):")
        for label, c in result["per_component"].items():
            print(f"  {label}: trades={c['n_trades']} final_cap={c['final_capital']:.0f}")
