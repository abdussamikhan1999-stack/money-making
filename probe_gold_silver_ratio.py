"""
Probe: gold/silver ratio mean reversion — a real, widely-traded commodity
pairs trade (unlike the equity pairs trading already tried and killed on
magnitude in this project's Fifth entry), on the premise that the two
metals' prices are economically linked (both precious/industrial hedges)
so their RATIO should mean-revert even when either price alone trends.
Genuinely different from equity pairs trading in two ways: a known,
publicly-followed ratio rather than a computed OLS hedge ratio, and this
project's two commodities (GC=F, SI=F) have each independently shown a
sizing-helps, quarter-stable edge before (IBS's gold survivor, SuperTrend's
oil survivor) — worth testing whether their RATIO does too, not just each
leg alone.

Method: ratio = gold_close / silver_close, z-scored over a rolling window
(mean/stdev of the ratio itself, no OLS regression needed since the ratio
IS the spread here). |z| > entry_z arms a trade (short gold/long silver if
the ratio is too high, long gold/short silver if too low); exit at |z| <
exit_z or a max-hold timeout. Equal notional both legs (not OLS-hedge-ratio-
weighted) — this is how the ratio is actually traded in practice, not an
equity-pairs-style regression fit. Same capital/instrument-unit convention
already used everywhere else in this project for GC=F/CL=F (rupee capital
figure applied directly to USD-denominated prices, no FX conversion) — see
probe_supertrend.py and daily_strategy.py's DonchianBreakoutStrategy tests.
"""
import argparse


def zscore_series(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(window, len(values)):
        w = values[i - window:i]
        mean = sum(w) / window
        var = sum((v - mean) ** 2 for v in w) / window
        std = var ** 0.5
        out[i] = (values[i] - mean) / std if std > 0 else None
    return out


def align(gold: list[dict], silver: list[dict]) -> list[dict]:
    """Inner-join by date; commodity futures sessions occasionally miss a
    day on one feed but not the other, so a naive zip would misalign."""
    silver_by_date = {b["date"].date(): b for b in silver}
    out = []
    for g in gold:
        s = silver_by_date.get(g["date"].date())
        if s is not None:
            out.append({"date": g["date"], "gold": g["close"], "silver": s["close"]})
    return out


def simulate(aligned: list[dict], window: int = 60, entry_z: float = 2.0, exit_z: float = 0.5,
             max_hold_days: int = 20, capital: float = 100_000.0, risk_per_trade_pct: float = 0.5,
             commission_per_leg: float = 20.0, max_drawdown_pct: float = 15.0):
    ratios = [a["gold"] / a["silver"] for a in aligned]
    z = zscore_series(ratios, window)

    capital_track = capital
    peak = capital
    max_dd = 0.0
    halted = False
    trades = []
    position = None  # dict: side ("short_ratio"/"long_ratio"), entry_gold, entry_silver, notional, days_in_trade

    for i in range(1, len(aligned)):
        if halted:
            break
        if z[i] is None:
            continue
        gold, silver = aligned[i]["gold"], aligned[i]["silver"]

        if position is not None:
            position["days_in_trade"] += 1
            gold_ret = (gold - position["entry_gold"]) / position["entry_gold"]
            silver_ret = (silver - position["entry_silver"]) / position["entry_silver"]
            if position["side"] == "short_ratio":  # short gold, long silver
                pnl = position["notional"] * (silver_ret - gold_ret)
            else:  # long_ratio: long gold, short silver
                pnl = position["notional"] * (gold_ret - silver_ret)

            reverted = abs(z[i]) < exit_z
            timed_out = position["days_in_trade"] >= max_hold_days
            if reverted or timed_out:
                net = pnl - 2 * commission_per_leg
                capital_track += net
                peak = max(peak, capital_track)
                dd = (peak - capital_track) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if max_dd >= max_drawdown_pct:
                    halted = True
                trades.append(dict(date=aligned[i]["date"], side=position["side"],
                                    outcome="revert" if reverted else "timeout",
                                    gross_pnl=pnl, net_pnl=net))
                position = None

        if position is None and not halted:
            if z[i] > entry_z or z[i] < -entry_z:
                notional = capital_track * risk_per_trade_pct / 100  # flat notional per leg — matches how this ratio is actually traded
                side = "short_ratio" if z[i] > entry_z else "long_ratio"
                position = dict(side=side, entry_gold=gold, entry_silver=silver,
                                 notional=notional, days_in_trade=0)

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


def walk_forward(aligned, split_ratio=0.5, **kwargs):
    n = len(aligned)
    cutoff = max(1, int(n * split_ratio))
    return simulate(aligned[:cutoff], **kwargs), simulate(aligned[cutoff:], **kwargs)


def quarter_split(aligned, **kwargs):
    n = len(aligned)
    chunk = max(1, n // 4)
    chunks = [aligned[i:i + chunk] for i in range(0, n, chunk)][:4]
    return [simulate(c, **kwargs) for c in chunks]


def fetch_daily_yfinance(symbol, period):
    from data_yfinance import fetch_candles
    return fetch_candles(symbol, "1d", period)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="10y")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--entry-z", type=float, default=2.0)
    parser.add_argument("--exit-z", type=float, default=0.5)
    parser.add_argument("--max-hold-days", type=int, default=20)
    parser.add_argument("--commission-per-leg", type=float, default=20.0)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--quarter-split", action="store_true")
    args = parser.parse_args()

    gold = fetch_daily_yfinance("GC=F", args.period)
    silver = fetch_daily_yfinance("SI=F", args.period)
    aligned = align(gold, silver)
    kwargs = dict(window=args.window, entry_z=args.entry_z, exit_z=args.exit_z,
                  max_hold_days=args.max_hold_days, capital=args.capital,
                  risk_per_trade_pct=args.risk_per_trade_pct, commission_per_leg=args.commission_per_leg)
    span_days = (aligned[-1]["date"] - aligned[0]["date"]).days
    years = span_days / 365.25

    if args.walk_forward:
        in_s, out_s = walk_forward(aligned, **kwargs)
        _report("In-sample  (first half)", in_s, args.capital, years / 2)
        _report("Out-of-sample (2nd half)", out_s, args.capital, years / 2)
        consistent = (in_s["total_net"] > 0) == (out_s["total_net"] > 0)
        print(f"{'CONSISTENT' if consistent else 'INCONSISTENT'} sign across halves")
    elif args.quarter_split:
        chunks = quarter_split(aligned, **kwargs)
        for i, c in enumerate(chunks):
            _report(f"Q{i+1}", c, args.capital, years / 4)
    else:
        result = simulate(aligned, **kwargs)
        _report("Full period", result, args.capital, years)
