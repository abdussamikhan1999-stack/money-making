"""
Probe: does MARKET REGIME (trend x volatility, see regime.py) improve this
project's own established survivors, or enable regime-based strategy
SWITCHING to beat running the single best one alone?

Two specific, motivated hypotheses (not a blind parameter sweep - per
CLAUDE.md's Twenty-eighth entry, a regime gate only ever rescued a
technique this project already knows tests well when the gate matches the
technique's own theoretical premise):

(a) FILTER: mean-reversion/counter-trend strategies (rsi2, turtlesoup)
    should do better restricted to LOW-vol regimes (calmer, range-bound
    conditions favor reversion); continuation/breakout strategies
    (threebar, macd) should do better restricted to HIGH-vol regimes
    (volatility expansion favors a breakout actually following through).
    Reuses probe_portfolio_combo.py's own COMPONENTS list - this project's
    5 best-known survivor (strategy, instrument) pairs - rather than
    picking new ones.

(b) SWITCH: on ONE instrument (AXISBANK.NS, Turtle Soup's cleanest single
    survivor - Twentieth entry), gate Turtle Soup (mean-reversion) to
    low-vol regimes and Donchian breakout (trend-following, not
    previously tested on this stock) to high-vol regimes, combine their
    trades chronologically on split capital (same dated_trades merge
    pattern as probe_portfolio_combo.py) - temporal allocation, a
    DIFFERENT shape from the Thirty-second entry's simultaneous
    fixed-weight blend, which failed because its components were too
    correlated.
"""
import argparse

from backtest_daily import fetch_daily_yfinance, simulate_daily, split_by_date
from backtest import _report
from daily_strategy import ConnorsRSI2Strategy, ThreeBarBreakoutStrategy, MACDStrategy, TurtleSoupStrategy, DonchianBreakoutStrategy
from probe_portfolio_combo import COMPONENTS, annualized_return_pct
from regime import RegimeClassifier, RegimeGatedStrategy, TRENDING_REGIMES, CHOPPY_REGIMES, current_regime

MEAN_REVERSION = {ConnorsRSI2Strategy, TurtleSoupStrategy}
CONTINUATION = {ThreeBarBreakoutStrategy, MACDStrategy}


def gate_for(strategy_cls) -> frozenset:
    if strategy_cls in MEAN_REVERSION:
        return CHOPPY_REGIMES
    if strategy_cls in CONTINUATION:
        return TRENDING_REGIMES
    raise ValueError(f"no regime hypothesis assigned for {strategy_cls}")


def run_filtered(symbol: str, strategy_cls, period: str, capital: float, risk_per_trade_pct: float,
                  commission_per_trade: float, classifier_kwargs=None, strategy_kwargs=None):
    daily = fetch_daily_yfinance(symbol, period)
    classifier_kwargs = classifier_kwargs or {}
    strategy_kwargs = strategy_kwargs or {}
    gated_factory = lambda: RegimeGatedStrategy(
        strategy_cls(**strategy_kwargs), RegimeClassifier(**classifier_kwargs), gate_for(strategy_cls))

    baseline = simulate_daily(daily, strategy_cls(**strategy_kwargs), capital=capital,
                               risk_per_trade_pct=risk_per_trade_pct, commission_per_trade=commission_per_trade)
    filtered = simulate_daily(daily, gated_factory(), capital=capital,
                               risk_per_trade_pct=risk_per_trade_pct, commission_per_trade=commission_per_trade)

    cutoff = sorted({c["date"].date() for c in daily})[max(1, int(len({c["date"].date() for c in daily}) * 0.5))]
    before, after = split_by_date(daily, cutoff)
    wf1 = simulate_daily(before, gated_factory(), capital=capital,
                          risk_per_trade_pct=risk_per_trade_pct, commission_per_trade=commission_per_trade)
    wf2 = simulate_daily(after, gated_factory(), capital=capital,
                          risk_per_trade_pct=risk_per_trade_pct, commission_per_trade=commission_per_trade)

    return dict(baseline=baseline, filtered=filtered, wf=(wf1, wf2))


def run_switch(symbol: str = "AXISBANK.NS", period: str = "10y", capital: float = 100_000.0,
                risk_per_trade_pct: float = 1.0, commission_per_trade: float = 20.0):
    daily = fetch_daily_yfinance(symbol, period)
    slice_capital = capital / 2
    dated_trades: list[tuple] = []
    per_component = {}

    components = [
        ("turtlesoup/choppy", lambda: RegimeGatedStrategy(TurtleSoupStrategy(), RegimeClassifier(), CHOPPY_REGIMES)),
        ("donchian/trending", lambda: RegimeGatedStrategy(DonchianBreakoutStrategy(), RegimeClassifier(), TRENDING_REGIMES)),
    ]
    for label, factory in components:
        trades: list[tuple] = []
        broker, risk = simulate_daily(daily, factory(), capital=slice_capital, risk_per_trade_pct=risk_per_trade_pct,
                                       commission_per_trade=commission_per_trade, dated_trades=trades)
        dated_trades.extend((date, pnl) for date, pnl in trades)
        per_component[label] = dict(final_capital=slice_capital + broker.cash_pnl, n_trades=len(broker.trade_log))

    dated_trades.sort(key=lambda t: t[0])
    capital_track, peak, max_dd = capital, capital, 0.0
    for date, pnl in dated_trades:
        capital_track += pnl
        peak = max(peak, capital_track)
        max_dd = max(max_dd, (peak - capital_track) / peak * 100 if peak > 0 else 0.0)

    years = (daily[-1]["date"] - daily[0]["date"]).days / 365.25
    return dict(final_capital=capital_track, max_dd=max_dd, n_trades=len(dated_trades),
                annualized_pct=annualized_return_pct(capital_track, capital, years),
                per_component=per_component)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["current", "filter", "switch"], default="current")
    parser.add_argument("--symbol", default="^NSEI")
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=1.0)
    parser.add_argument("--commission-per-trade", type=float, default=20.0)
    args = parser.parse_args()

    if args.mode == "current":
        daily = fetch_daily_yfinance(args.symbol, args.period)
        print(f"{args.symbol} current regime as of {daily[-1]['date'].date()}: {current_regime(daily)}")

    elif args.mode == "filter":
        for label, strategy_cls, symbol, kwargs in COMPONENTS:
            result = run_filtered(symbol, strategy_cls, args.period, args.capital,
                                   args.risk_per_trade_pct, args.commission_per_trade, strategy_kwargs=kwargs)
            (b_broker, b_risk), (f_broker, f_risk) = result["baseline"], result["filtered"]
            (w1b, w1r), (w2b, w2r) = result["wf"]
            print(f"\n=== {label} (gate={sorted(gate_for(strategy_cls))}) ===")
            print(f"  baseline: pnl={b_broker.cash_pnl:.0f} trades={len(b_broker.trade_log)} dd={b_risk.drawdown_from_peak_pct:.1f}%")
            print(f"  filtered: pnl={f_broker.cash_pnl:.0f} trades={len(f_broker.trade_log)} dd={f_risk.drawdown_from_peak_pct:.1f}%")
            print(f"  filtered walk-forward: half1 pnl={w1b.cash_pnl:.0f} (halted={w1r.drawdown_halted}) "
                  f"half2 pnl={w2b.cash_pnl:.0f} (halted={w2r.drawdown_halted})")

    elif args.mode == "switch":
        result = run_switch(period=args.period, capital=args.capital,
                             risk_per_trade_pct=args.risk_per_trade_pct, commission_per_trade=args.commission_per_trade)
        print(f"Regime-switched (AXISBANK.NS): final_cap={result['final_capital']:.0f} "
              f"max_dd={result['max_dd']:.1f}% annualized={result['annualized_pct']:.2f}%/yr trades={result['n_trades']}")
        for label, c in result["per_component"].items():
            print(f"  {label}: final_cap={c['final_capital']:.0f} trades={c['n_trades']}")
