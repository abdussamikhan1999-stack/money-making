"""
Market-regime classifier: TREND (today's close vs a trailing SMA) crossed
with VOLATILITY (today's realized-vol reading ranked within its own trailing
lookback window) -> 4 buckets (up_high_vol, up_low_vol, down_high_vol,
down_low_vol). Reuses indicators.py's existing sma()/stdev() rather than
adding a new dependency (ADX, HMM regime-switching libraries, etc. were all
passed over - this project has consistently favored boring, explainable
indicators, and a 200-day-SMA trend read is the same convention
ConnorsRSI2Strategy's own trend filter already established and validated).

No-lookahead convention matches every daily_strategy.py strategy exactly:
classify(close) is called BEFORE push(bar) for that same day, using only
bars pushed through yesterday for both the SMA and the volatility-percentile
windows - the trend comparison uses today's close (same as RSI-2's own
trend-SMA check), the vol reading does not.
"""
from dataclasses import dataclass, field

from indicators import sma, stdev

TREND_UP = "up"
TREND_DOWN = "down"
VOL_HIGH = "high_vol"
VOL_LOW = "low_vol"

ALL_REGIMES = frozenset(f"{t}_{v}" for t in (TREND_UP, TREND_DOWN) for v in (VOL_HIGH, VOL_LOW))
TRENDING_REGIMES = frozenset(f"{t}_{VOL_HIGH}" for t in (TREND_UP, TREND_DOWN))
CHOPPY_REGIMES = frozenset(f"{t}_{VOL_LOW}" for t in (TREND_UP, TREND_DOWN))


@dataclass
class RegimeClassifier:
    trend_period: int = 200
    vol_period: int = 20
    vol_lookback: int = 252  # ~1 trading year; the vol reading is ranked within this trailing window

    _closes: list[float] = field(default_factory=list, init=False)
    _returns: list[float] = field(default_factory=list, init=False)
    _vols: list[float] = field(default_factory=list, init=False)

    def reset(self) -> None:
        self._closes, self._returns, self._vols = [], [], []

    def push(self, bar: dict) -> None:
        close = bar["close"]
        if self._closes:
            self._returns.append((close - self._closes[-1]) / self._closes[-1])
        self._closes.append(close)
        v = stdev(self._returns, period=self.vol_period)
        if v is not None:
            self._vols.append(v)

    def trend_regime(self, close: float) -> str | None:
        if len(self._closes) < self.trend_period:
            return None
        trend_sma = sma(self._closes[-self.trend_period:])
        return TREND_UP if close > trend_sma else TREND_DOWN

    def vol_regime(self, close: float) -> str | None:
        if len(self._vols) < 2:
            return None
        window = self._vols[-self.vol_lookback:]
        current = window[-1]
        rank = sum(1 for v in window if v <= current) / len(window)
        return VOL_HIGH if rank >= 0.5 else VOL_LOW

    def classify(self, close: float) -> str | None:
        trend, vol = self.trend_regime(close), self.vol_regime(close)
        return None if trend is None or vol is None else f"{trend}_{vol}"


def current_regime(daily: list[dict], **kwargs) -> str | None:
    """Regime as of the LAST bar in `daily`, using only prior bars for both
    windows (no lookahead) - the same "what does today look like" question a
    trader would ask each morning."""
    if not daily:
        return None
    c = RegimeClassifier(**kwargs)
    for bar in daily[:-1]:
        c.push(bar)
    return c.classify(daily[-1]["close"])


@dataclass
class RegimeGatedStrategy:
    """Wraps any daily_strategy.py strategy (push/check_entry/check_exit) and
    only lets check_entry() return a signal when the CURRENT regime is in
    `allowed_regimes` - a general-purpose regime filter usable on any
    already-tested strategy without modifying it. Generalizes CLAUDE.md's
    Twenty-eighth entry (a strategy-specific trend-drift gate bolted onto
    BollingerBandsStrategy): a single regime GATE on an existing entry
    trigger, not a second signal stacked into the trade - already shown to
    be the shape that CAN help (Twenty-eighth), unlike signal-stacking
    (Eleventh, Twenty-second)."""
    strategy: object
    classifier: RegimeClassifier
    allowed_regimes: frozenset

    def reset(self) -> None:
        self.strategy.reset()
        self.classifier.reset()

    def push(self, bar: dict) -> None:
        self.strategy.push(bar)
        self.classifier.push(bar)

    def check_entry(self, close: float):
        regime = self.classifier.classify(close)
        if regime is None or regime not in self.allowed_regimes:
            return None
        return self.strategy.check_entry(close)

    def check_exit(self, close: float, side) -> bool:
        return self.strategy.check_exit(close, side)

    def current_atr(self):
        fn = getattr(self.strategy, "current_atr", None)
        return fn() if fn else None


if __name__ == "__main__":
    import argparse
    from backtest_daily import fetch_daily_yfinance

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="^NSEI")
    parser.add_argument("--period", default="3y")
    args = parser.parse_args()
    daily = fetch_daily_yfinance(args.symbol, args.period)
    regime = current_regime(daily)
    print(f"{args.symbol} current regime as of {daily[-1]['date'].date()} "
          f"(close={daily[-1]['close']:.2f}): {regime}")
