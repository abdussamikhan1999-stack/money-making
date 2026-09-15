"""
Daily-bar strategies: a second family, genuinely different from the
intraday Highest-Open/Lowest-Open family in strategy.py (different
mechanisms, different timeframe — one bar per day, not H1/M15/tick — and
backtested against YEARS of daily history instead of yfinance's 60-day
intraday cap).

All strategies here share one interface so backtest_daily.py's engine can
run any of them interchangeably:
  - push(bar: dict)        record one day's OHLC (call every day)
  - check_entry(close)     call only when flat; returns Signal | None
  - check_exit(close, side) call only while in a position; returns bool
"""
from dataclasses import dataclass, field

from strategy import Signal, Side
from indicators import average_true_range, sma, rsi


@dataclass
class DonchianBreakoutStrategy:
    """N-day high/low channel breakout — the core mechanic of the "Turtle
    Trading" system, one of the most well-documented trend-following
    approaches that exists. Long when price closes above the highest high of
    the prior `entry_period` days; short when it closes below the lowest low.
    One position at a time.

    Uses TWO channels, the classic Turtle shape:
      - `entry_period` (default 20): the entry breakout, and also the
        initial hard stop-loss (the opposite side of the SAME channel).
      - `exit_period` (default entry_period // 2): a SHORTER channel used
        only to exit an open position early, in the trend's own direction,
        once it breaks back through — the standard way a trend system lets
        a winner run rather than capping it with a fixed-point trail.

    Deliberately does NOT use strategy.py's TrailingStopManager (fixed
    breakeven/trail point thresholds) — those are tuned for forex pips and
    are far smaller than a single day's typical range on most instruments
    this is used against, which silently converts "let the trend run" into
    "exit for a manufactured near-zero profit within hours of entry." A
    concrete case of this bug produced a fake 100% win rate on ETH-USD
    before it was caught — see CLAUDE.md. Position management here is:
    hold until either the initial structural stop is hit, or check_exit()
    fires; nothing in between narrows the stop early.

    Uses the window BEFORE today's bar (today's own high/low is excluded)
    so there's no lookahead — a breakout is genuinely "new", not today's
    bar comparing against itself.
    """
    entry_period: int = 20
    exit_period: int = 0  # 0 = default to entry_period // 2 (classic Turtle ratio), set in __post_init__

    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.exit_period <= 0:
            self.exit_period = max(1, self.entry_period // 2)

    def reset(self) -> None:
        self._highs = []
        self._lows = []

    def push(self, bar: dict) -> None:
        """Record one day's bar into the rolling window. Call this once per
        day regardless of whether a position is open — the channel must
        keep moving even while in a trade."""
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])

    def check_entry(self, close: float) -> Signal | None:
        """Call only when flat. Compares today's close against the channel
        built from the PRIOR entry_period days (push() for today not yet
        called when this runs)."""
        if len(self._highs) < self.entry_period:
            return None
        window_high = max(self._highs[-self.entry_period:])
        window_low = min(self._lows[-self.entry_period:])
        if close > window_high:
            return Signal(Side.LONG, close, window_low, f"{self.entry_period}-day high breakout")
        if close < window_low:
            return Signal(Side.SHORT, close, window_high, f"{self.entry_period}-day low breakdown")
        return None

    def check_exit(self, close: float, side: Side) -> bool:
        """Call only while in a position. True once price closes back
        through the SHORTER exit_period channel against the position's own
        direction — the trend-following exit, distinct from the initial
        hard stop (which the caller enforces separately, e.g. via
        PaperBroker's own stop-hit check)."""
        if len(self._lows) < self.exit_period:
            return False
        if side == Side.LONG:
            return close < min(self._lows[-self.exit_period:])
        return close > max(self._highs[-self.exit_period:])


@dataclass
class ConnorsRSI2Strategy:
    """Larry Connors' 2-period RSI mean-reversion strategy (Connors &
    Alvarez, "Short Term Trading Strategies That Work") — widely cited as
    having a documented statistical edge that has persisted across decades
    of market data. Genuinely different mechanism from DonchianBreakoutStrategy:
    SHORT-TERM MEAN REVERSION filtered by a LONG-TERM trend (buy oversold
    dips in an uptrend, sell overbought rallies in a downtrend), not
    trend-following price extremes.

    The standard, widely-published rules:
      - Trend filter: `trend_period`-day SMA (classic: 200). Only go long
        above it, only go short below it.
      - Long entry: RSI(`rsi_period`) < `rsi_entry_long` (classic: 5) AND
        close > trend SMA.
      - Short entry: RSI(`rsi_period`) > `rsi_entry_short` (classic: 95)
        AND close < trend SMA.
      - Exit: close crosses back through the `exit_sma_period`-day SMA
        (classic: 5 — the mean-reversion target), OR the trend filter
        itself flips against the position.

    Unlike Donchian, this rule has no natural structural stop, so the
    initial stop-loss is `stop_atr_multiple` x ATR(`atr_period`) from
    entry — a standard volatility-based stop (Carver-style), not part of
    Connors' original publication but necessary for this repo's risk
    sizing, which requires a numeric stop_loss on every Signal.
    """
    rsi_period: int = 2
    trend_period: int = 200
    exit_sma_period: int = 5
    rsi_entry_long: float = 5.0
    rsi_entry_short: float = 95.0
    stop_atr_multiple: float = 3.0
    atr_period: int = 14

    _closes: list[float] = field(default_factory=list, init=False)
    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)

    def reset(self) -> None:
        self._closes = []
        self._highs = []
        self._lows = []

    def push(self, bar: dict) -> None:
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])
        self._closes.append(bar["close"])

    def _current_atr(self) -> float | None:
        n = self.atr_period + 1
        if len(self._closes) < n:
            return None
        window = [{"high": h, "low": l, "close": c} for h, l, c in
                  zip(self._highs[-n:], self._lows[-n:], self._closes[-n:])]
        return average_true_range(window, period=self.atr_period)

    def current_atr(self) -> float | None:
        """Public wrapper so a caller (e.g. backtest_daily.py's profit-booking
        overlay) can scale a trailing-stop's trigger distance to THIS
        instrument's actual volatility at entry time, rather than reusing
        strategy.py's forex-pip-scale defaults — see CLAUDE.md's note on the
        fake-100%-win-rate bug that unscaled defaults caused here before."""
        return self._current_atr()

    def check_entry(self, close: float) -> Signal | None:
        """Uses the window BEFORE today for the trend SMA and ATR (push()
        for today not yet called) — same no-lookahead convention as
        DonchianBreakoutStrategy. RSI(2) is the one exception: it's
        specifically about very recent price action, so it must include
        TODAY's close (passed explicitly here) or "detect today's oversold
        dip" would actually mean "detect yesterday's," one day stale."""
        if len(self._closes) < self.trend_period:
            return None
        trend_sma = sma(self._closes[-self.trend_period:])
        r = rsi(self._closes + [close], period=self.rsi_period)
        atr = self._current_atr()
        if r is None or atr is None or atr <= 0:
            return None
        if close > trend_sma and r < self.rsi_entry_long:
            return Signal(Side.LONG, close, close - self.stop_atr_multiple * atr,
                          f"RSI({self.rsi_period})={r:.1f} oversold above {self.trend_period}d SMA")
        if close < trend_sma and r > self.rsi_entry_short:
            return Signal(Side.SHORT, close, close + self.stop_atr_multiple * atr,
                          f"RSI({self.rsi_period})={r:.1f} overbought below {self.trend_period}d SMA")
        return None

    def check_exit(self, close: float, side: Side) -> bool:
        if len(self._closes) < max(self.exit_sma_period, self.trend_period):
            return False
        exit_sma = sma(self._closes[-self.exit_sma_period:])
        trend_sma = sma(self._closes[-self.trend_period:])
        if side == Side.LONG:
            return close > exit_sma or close < trend_sma
        return close < exit_sma or close > trend_sma
