"""
Highest Open / Lowest Open intraday reversal strategy (source: a well-known
ForexFactory thread, adapted here from forex/H1 to whatever instrument +
opening-timeframe you feed it via Kite).

Rule, as specified:
  - Track the highest and lowest opening-bar open price seen so far today
    (the source uses H1 opens; the timeframe is a runner-level choice, this
    module just consumes whatever "bar opens" it's given).
  - SHORT: once price trades ABOVE the highest-open line, arm a short.
    The first time price then falls back DOWN through that same line, enter.
  - LONG: mirror — once price trades BELOW the lowest-open line, arm a long.
    The first time price then rises back UP through that line, enter.
  - Initial stop-loss = the day's high-so-far (short) / low-so-far (long) —
    i.e. the peak/trough of the move that armed the trade.
  - Entries are evaluated on every price update, not on bar close (per the
    source: "do not wait for the bar to close to enter a trade").
  - Optional filter (added later in the source thread): also require the
    concurrent M15 bar's open to be beyond the line before entering.

Interpretation choices made here (the source is prose, not code — flag if
these don't match your intent):
  - `one_shot_per_level` (default True): a given line value only fires once.
    If a later bar's open pushes the line further, the line moves and can
    fire again at the new level. This matches the source's emphasis on
    patience/waiting rather than repeated re-entries at an unchanged level.
  - Take-profit/trailing-stop management (breakeven at +5, trail at +10) is
    implemented in `TrailingStopManager` below, generalised to price *units*
    instead of forex pips — pick thresholds that make sense for whatever
    instrument you point this at.

This module is pure and network-free by design: no Kite/broker imports.
See data.py / kite_client.py / paper_broker.py / run_live.py for wiring.
"""

from dataclasses import dataclass, field
from enum import Enum


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Signal:
    side: Side
    entry_price: float
    stop_loss: float
    reason: str


@dataclass
class HighLowOpenStrategy:
    m15_filter: bool = False
    one_shot_per_level: bool = True

    highest_open: float | None = field(default=None, init=False)
    lowest_open: float | None = field(default=None, init=False)
    day_high: float = field(default=float("-inf"), init=False)
    day_low: float = field(default=float("inf"), init=False)

    _short_armed: bool = field(default=False, init=False)
    _long_armed: bool = field(default=False, init=False)
    _short_fired_at: float | None = field(default=None, init=False)
    _long_fired_at: float | None = field(default=None, init=False)

    def reset_session(self) -> None:
        """Call once at the start of each trading day."""
        self.highest_open = None
        self.lowest_open = None
        self.day_high = float("-inf")
        self.day_low = float("inf")
        self._short_armed = self._long_armed = False
        self._short_fired_at = self._long_fired_at = None

    def on_bar_open(self, open_price: float) -> None:
        """Feed each new opening-timeframe bar's open (default: H1) as it forms."""
        if self.highest_open is None or open_price > self.highest_open:
            self.highest_open = open_price
            self._short_armed = False
            self._short_fired_at = None
        if self.lowest_open is None or open_price < self.lowest_open:
            self.lowest_open = open_price
            self._long_armed = False
            self._long_fired_at = None

    def on_price(self, ltp: float, m15_open: float | None = None) -> Signal | None:
        """Feed every price update. Returns a Signal the instant an entry fires."""
        self.day_high = max(self.day_high, ltp)
        self.day_low = min(self.day_low, ltp)

        if self.highest_open is not None:
            if ltp > self.highest_open:
                self._short_armed = True
            elif self._short_armed and ltp <= self.highest_open:
                already_fired = self.one_shot_per_level and self._short_fired_at == self.highest_open
                filter_ok = not self.m15_filter or (m15_open is not None and m15_open > self.highest_open)
                if not already_fired and filter_ok:
                    self._short_fired_at = self.highest_open
                    self._short_armed = False
                    return Signal(Side.SHORT, self.highest_open, self.day_high,
                                  "price broke above the highest-open line and returned")

        if self.lowest_open is not None:
            if ltp < self.lowest_open:
                self._long_armed = True
            elif self._long_armed and ltp >= self.lowest_open:
                already_fired = self.one_shot_per_level and self._long_fired_at == self.lowest_open
                filter_ok = not self.m15_filter or (m15_open is not None and m15_open < self.lowest_open)
                if not already_fired and filter_ok:
                    self._long_fired_at = self.lowest_open
                    self._long_armed = False
                    return Signal(Side.LONG, self.lowest_open, self.day_low,
                                  "price broke below the lowest-open line and returned")
        return None


@dataclass
class HighLowOpenBreakoutStrategy:
    """Trend-following INVERSE of HighLowOpenStrategy: instead of fading a
    breakout back through the line, this trades WITH it — buy the instant
    price breaks above the highest-open line, sell the instant it breaks
    below the lowest-open line. Stop-loss is the line itself (if the
    breakout fails and price falls back through, the premise was wrong —
    get out), not the reversal rule's day-high/low stop.

    Not from the source thread — built to test a hypothesis the reversal
    rule's own backtest results raised: a strongly trending instrument
    (oil, currently) looks like a bad fit for a mean-reversion rule and a
    plausible fit for a trend-following one. Compare the two on the same
    instrument via backtest.py's --variant flag before drawing conclusions
    from either alone.
    """
    one_shot_per_level: bool = True

    highest_open: float | None = field(default=None, init=False)
    lowest_open: float | None = field(default=None, init=False)

    _long_fired_at: float | None = field(default=None, init=False)
    _short_fired_at: float | None = field(default=None, init=False)

    def reset_session(self) -> None:
        self.highest_open = None
        self.lowest_open = None
        self._long_fired_at = None
        self._short_fired_at = None

    def on_bar_open(self, open_price: float) -> None:
        if self.highest_open is None or open_price > self.highest_open:
            self.highest_open = open_price
            self._long_fired_at = None
        if self.lowest_open is None or open_price < self.lowest_open:
            self.lowest_open = open_price
            self._short_fired_at = None

    def on_price(self, ltp: float, m15_open: float | None = None) -> Signal | None:
        """m15_open is accepted but ignored — kept only so this is
        interchangeable with HighLowOpenStrategy in backtest.py's loop."""
        if self.highest_open is not None and ltp > self.highest_open:
            already_fired = self.one_shot_per_level and self._long_fired_at == self.highest_open
            if not already_fired:
                self._long_fired_at = self.highest_open
                return Signal(Side.LONG, ltp, self.highest_open,
                              "price broke above the highest-open line — trading WITH the breakout")

        if self.lowest_open is not None and ltp < self.lowest_open:
            already_fired = self.one_shot_per_level and self._short_fired_at == self.lowest_open
            if not already_fired:
                self._short_fired_at = self.lowest_open
                return Signal(Side.SHORT, ltp, self.lowest_open,
                              "price broke below the lowest-open line — trading WITH the breakdown")
        return None


@dataclass
class TrailingStopManager:
    """Stop-management: move to breakeven+offset after `breakeven_trigger`
    units of profit, then trail to entry+trail_offset after `trail_trigger`.
    Units are whatever price units your instrument uses — tune per instrument,
    the source thread's 5/10 pip defaults won't mean much outside forex."""

    entry_price: float
    initial_stop: float
    side: Side
    breakeven_trigger: float = 5.0
    breakeven_offset: float = 1.0
    trail_trigger: float = 10.0
    trail_offset: float = 5.0

    stop: float = field(init=False)

    def __post_init__(self) -> None:
        self.stop = self.initial_stop

    def _profit(self, ltp: float) -> float:
        return (ltp - self.entry_price) if self.side == Side.LONG else (self.entry_price - ltp)

    def update(self, ltp: float) -> float:
        """Call on every price update; returns the (possibly tightened) stop."""
        profit = self._profit(ltp)
        direction = 1 if self.side == Side.LONG else -1

        if profit >= self.trail_trigger:
            candidate = self.entry_price + direction * self.trail_offset
        elif profit >= self.breakeven_trigger:
            candidate = self.entry_price + direction * self.breakeven_offset
        else:
            candidate = self.stop

        self.stop = max(self.stop, candidate) if self.side == Side.LONG else min(self.stop, candidate)
        return self.stop

    def hit(self, ltp: float) -> bool:
        return ltp <= self.stop if self.side == Side.LONG else ltp >= self.stop
