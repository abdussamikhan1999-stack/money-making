"""
Daily-bar trend-following: a genuinely different mechanism and timeframe
from the intraday Highest-Open/Lowest-Open family in strategy.py. Operates
on one daily bar per day (not H1/M15/tick data), holds positions across
many days, and is backtested against YEARS of daily history instead of
yfinance's 60-day intraday cap — escaping the single-window limitation that
was the biggest weakness of every test run against the other family.
"""
from dataclasses import dataclass, field

from strategy import Signal, Side


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

    def push(self, high: float, low: float) -> None:
        """Record one day's high/low into the rolling window. Call this once
        per day regardless of whether a position is open — the channel must
        keep moving even while in a trade."""
        self._highs.append(high)
        self._lows.append(low)

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
