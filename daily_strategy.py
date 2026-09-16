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
from indicators import (
    average_true_range, sma, rsi, stdev, highest, lowest, linreg,
    chaikin_money_flow, on_balance_volume, ema_update,
)


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


@dataclass
class ThreeBarBreakoutStrategy:
    """3-bar range-compression breakout/continuation pattern, sourced from
    ForexFactory's "Daily chart trading - simple entry and exit criteria"
    thread (thread 1126565) and the related "Daily Chart 3-Candle" /
    "3 Consecutive Candles Method" threads (759887, 758687). Genuinely
    different mechanism from everything else in this file: not a channel
    breakout (Donchian) or mean-reversion (RSI-2), but a short-horizon
    (3-day) pattern-continuation signal with a FIXED profit target -
    every other strategy here exits on a moving condition instead.

    Original rule (forex, stated in pips): two prior bars ("bar1", "bar2")
    closing near the same level (compression - source: "win probability is
    higher when bar1 and bar2 prices are close to or at the same level")
    followed by a third bar ("bar3", today) closing decisively beyond BOTH
    signals continuation in that direction. Stop placed a fixed pip buffer
    beyond bar2's structural low/high (source: "50 pips or 15 pips below
    bar2's low, whichever is greater" - i.e. a fixed buffer past the
    breakout structure). Take-profit a fixed pip target (source: "average
    take profit 100-200 pips" against a "50-65 pip" implied risk, roughly
    2-3R).

    Generalized to price units via ATR instead of raw forex pips - the same
    fix this project already had to apply to strategy.py's
    TrailingStopManager defaults elsewhere (see CLAUDE.md's fake-100%-win-
    rate bug: unscaled pip-sized numbers are meaningless on a non-forex
    instrument):
      - compression_atr_mult (default 0.5): bar1/bar2 closes must be within
        this many ATRs of each other.
      - stop_buffer_atr_mult (default 0.5): stop placed this many ATRs
        beyond bar2's structural low/high.
      - target_r_multiple (default 2.5): fixed take-profit at this multiple
        of the entry's own risk - the middle of the source's implied 2-3R
        range.
      - max_hold_days (default 20): NOT in the source thread - added
        because a fixed-target trade with no time stop can sit open
        indefinitely if price drifts sideways. Every other strategy in this
        file has some bounded holding mechanism (Donchian's exit channel,
        RSI-2's SMA cross, pairs trading's 20-day cap in the probe script);
        an unbounded hold here would be the odd one out.

    Uses the window BEFORE today's bar for bar1/bar2 and ATR (today's own
    high/low not yet pushed) - same no-lookahead convention as Donchian and
    RSI-2. bar3 is `close`, the argument, standing in for today's not-yet-
    pushed bar.
    """
    compression_atr_mult: float = 0.5
    stop_buffer_atr_mult: float = 0.5
    target_r_multiple: float = 2.5
    max_hold_days: int = 20
    atr_period: int = 14

    _closes: list[float] = field(default_factory=list, init=False)
    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)
    _target: float | None = field(default=None, init=False)
    _days_in_trade: int = field(default=0, init=False)

    def reset(self) -> None:
        self._closes = []
        self._highs = []
        self._lows = []
        self._target = None
        self._days_in_trade = 0

    def push(self, bar: dict) -> None:
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])
        self._closes.append(bar["close"])
        if self._target is not None:
            self._days_in_trade += 1

    def _current_atr(self) -> float | None:
        n = self.atr_period + 1
        if len(self._closes) < n:
            return None
        window = [{"high": h, "low": l, "close": c} for h, l, c in
                  zip(self._highs[-n:], self._lows[-n:], self._closes[-n:])]
        return average_true_range(window, period=self.atr_period)

    def current_atr(self) -> float | None:
        """Public wrapper, same purpose as ConnorsRSI2Strategy's: lets a
        caller scale anything to THIS entry's own volatility rather than a
        forex-pip-scale constant."""
        return self._current_atr()

    def check_entry(self, close: float) -> Signal | None:
        if len(self._closes) < 2:
            return None
        atr = self._current_atr()
        if atr is None or atr <= 0:
            return None

        bar1_close, bar2_close = self._closes[-2], self._closes[-1]
        bar2_low, bar2_high = self._lows[-1], self._highs[-1]

        if abs(bar1_close - bar2_close) > self.compression_atr_mult * atr:
            return None  # no compression - source's higher-win-probability filter

        if close > bar1_close and close > bar2_close:
            stop = bar2_low - self.stop_buffer_atr_mult * atr
            risk = close - stop
            self._target = close + self.target_r_multiple * risk
            self._days_in_trade = 0
            return Signal(Side.LONG, close, stop, "3-bar compression breakout (long)")

        if close < bar1_close and close < bar2_close:
            stop = bar2_high + self.stop_buffer_atr_mult * atr
            risk = stop - close
            self._target = close - self.target_r_multiple * risk
            self._days_in_trade = 0
            return Signal(Side.SHORT, close, stop, "3-bar compression breakdown (short)")

        return None

    def check_exit(self, close: float, side: Side) -> bool:
        hit_target = (close >= self._target) if side == Side.LONG else (close <= self._target)
        timed_out = self._days_in_trade >= self.max_hold_days
        if hit_target or timed_out:
            self._target = None
            return True
        return False


@dataclass
class SqueezeMomentumStrategy:
    """John Carter's TTM Squeeze, in the exact formulation of LazyBear's
    "Squeeze Momentum Indicator [LazyBear]" — the canonical open-source
    implementation (originally published as a public TradingView Pine
    script; mirrored/forked widely, e.g. fmzquant/strategies'
    Squeeze-Momentum-Indicator.md and indie-script.github.io's Squeeze
    Momentum writeup). Genuinely different mechanism from everything else
    in this file: it isn't a price-channel breakout (Donchian), a level-
    based mean reversion (RSI-2), or a fixed-pattern continuation (3-bar
    breakout) — it's a VOLATILITY-STATE timing signal. The core idea: when
    Bollinger Bands contract to sit entirely inside Keltner Channels, the
    market is coiled ("squeeze on"); when BB expands back outside KC
    ("squeeze fires off"), that release tends to be followed by a directional
    move, and a linear-regression-based momentum value gives the direction.

    Rules, reproduced exactly from LazyBear's script (bb_length =
    kc_length = `length` here, matching the original's shared default of
    20; `bb_mult`/`kc_mult` default 2.0/1.5, also the original's defaults):
      - basis = SMA(close, length); dev = bb_mult * stdev(close, length)
        -> upperBB = basis + dev, lowerBB = basis - dev
      - ma = SMA(close, length); rangema = ATR(length) (this project's
        average_true_range() is already the "simple/unsmoothed ATR" the
        original script uses for its KC range component, since it just
        SMAs the true-range series — no adaptation needed)
        -> upperKC = ma + rangema * kc_mult, lowerKC = ma - rangema * kc_mult
      - sqzOn = (lowerBB > lowerKC) and (upperBB < upperKC)
      - val = linreg(close - avg(avg(highest(high,length), lowest(low,length)),
              SMA(close,length))) over the same `length` window — the
        "momentum" histogram; its SIGN gives direction, not its magnitude.

    Entry: the trading rule everyone actually cites for this indicator
    (Carter's own writeups, every retail breakdown) is "wait for the squeeze
    to fire (go from on to off) and enter in the direction of the momentum
    value" — so entry fires exactly on the sqzOn(prev) -> not-sqzOn(now)
    transition, sign(val) at that same bar sets direction. No natural
    structural stop exists (same problem RSI-2 had), so the initial stop is
    `stop_atr_multiple` x ATR(length) — reusing the same ATR already
    computed for the Keltner Channel rather than adding a second ATR window.

    Exit: momentum flipping sign against the position (the standard "first
    opposite-color bar" exit cited everywhere for this indicator) OR
    max_hold_days as a hard time-stop — this file's now-standard bounded-
    holding convention (see ThreeBarBreakoutStrategy's docstring for why
    every strategy here needs one).

    Unlike RSI-2 (which must see today's own close to detect today's dip),
    sqzOn/val here are computed ENTIRELY from the `length` days already
    pushed — today's close is used only as the price actually traded at,
    never fed into the rolling window. This is the Donchian/exit-channel
    convention rather than RSI-2's: a squeeze/BB/KC calculation is a slow,
    20-bar-wide lagging statistic, so being one bar "behind" doesn't change
    its character the way it would for a 2-period RSI. Same no-lookahead
    principle as every other strategy here either way.
    """
    length: int = 20
    bb_mult: float = 2.0
    kc_mult: float = 1.5
    stop_atr_multiple: float = 2.0
    max_hold_days: int = 20

    _closes: list[float] = field(default_factory=list, init=False)
    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)
    _prev_sqz_on: bool | None = field(default=None, init=False)
    _in_trade: bool = field(default=False, init=False)
    _days_in_trade: int = field(default=0, init=False)
    _last_atr: float | None = field(default=None, init=False)

    def reset(self) -> None:
        self._closes = []
        self._highs = []
        self._lows = []
        self._prev_sqz_on = None
        self._in_trade = False
        self._days_in_trade = 0
        self._last_atr = None

    def push(self, bar: dict) -> None:
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])
        self._closes.append(bar["close"])
        if self._in_trade:
            self._days_in_trade += 1

    def current_atr(self) -> float | None:
        """Same public-wrapper convention as ConnorsRSI2Strategy/
        ThreeBarBreakoutStrategy: the Keltner Channel's own ATR (its range
        component), reused rather than computing a second one."""
        return self._last_atr

    def _compute(self) -> tuple[bool | None, bool, float | None] | None:
        """(prev_sqz_on, sqz_on, val) from the `length` days already pushed
        — today NOT included, same no-lookahead convention as Donchian/
        RSI-2 (called from check_entry/check_exit BEFORE push() runs for
        today). Updates self._prev_sqz_on/self._last_atr as a side effect,
        exactly once per day regardless of whether check_entry or
        check_exit is the caller — simulate_daily's if/elif calls exactly
        one of them per bar, so this is still called once per day and the
        sqzOn(prev) -> sqzOn(now) transition tracking stays correct even
        while a position spans multiple days of check_exit-only calls.
        Needs length+1 closes so average_true_range (itself needing
        period+1 candles) has a full window."""
        if len(self._closes) < self.length + 1:
            return None
        window_closes = self._closes[-self.length:]
        window_highs = self._highs[-self.length:]
        window_lows = self._lows[-self.length:]

        basis = sma(window_closes)
        dev = self.bb_mult * stdev(window_closes)
        upper_bb, lower_bb = basis + dev, basis - dev

        atr_candles = [{"high": h, "low": l, "close": c} for h, l, c in
                       zip(self._highs[-(self.length + 1):], self._lows[-(self.length + 1):],
                           self._closes[-(self.length + 1):])]
        range_ma = average_true_range(atr_candles, period=self.length)
        self._last_atr = range_ma
        upper_kc, lower_kc = basis + range_ma * self.kc_mult, basis - range_ma * self.kc_mult

        sqz_on = (lower_bb > lower_kc) and (upper_bb < upper_kc)

        hh, ll = highest(window_highs, self.length), lowest(window_lows, self.length)
        donchian_mid = (hh + ll) / 2
        centered = [c - (donchian_mid + basis) / 2 for c in window_closes]
        val = linreg(centered)

        prev_sqz_on = self._prev_sqz_on
        self._prev_sqz_on = sqz_on
        return prev_sqz_on, sqz_on, val

    def check_entry(self, close: float) -> Signal | None:
        result = self._compute()
        if result is None:
            return None
        prev_sqz_on, sqz_on, val = result

        if prev_sqz_on is True and not sqz_on and val is not None and val != 0 and self._last_atr:
            self._in_trade = True
            self._days_in_trade = 0
            if val > 0:
                return Signal(Side.LONG, close, close - self.stop_atr_multiple * self._last_atr,
                              "TTM Squeeze fired long (momentum positive)")
            return Signal(Side.SHORT, close, close + self.stop_atr_multiple * self._last_atr,
                          "TTM Squeeze fired short (momentum negative)")
        return None

    def check_exit(self, close: float, side: Side) -> bool:
        result = self._compute()
        timed_out = self._days_in_trade >= self.max_hold_days
        val = result[2] if result is not None else None
        momentum_flipped = val is not None and ((val < 0) if side == Side.LONG else (val > 0))
        if momentum_flipped or timed_out:
            self._in_trade = False
            return True
        return False


@dataclass
class VolumeConfirmationStrategy:
    """Chaikin Money Flow + On-Balance Volume dual confirmation, reproducing
    XBT3K/VOLUME-ALGO-EURUSD's `VolumeOBVCMF` backtrader strategy
    (github.com/XBT3K/VOLUME-ALGO-EURUSD) — a real, runnable open-source
    strategy implementation, per the user's request to keep sourcing new
    mechanisms from actual strategy CODE rather than forum rule
    descriptions (see SqueezeMomentumStrategy's docstring, same standard).
    Genuinely different mechanism from everything else in this file: it's
    the first strategy here to use VOLUME/money-flow at all — everything
    tried before this (Donchian, RSI-2, 3-bar breakout, Squeeze) is
    price-only.

    Source rule (long-only in the original): buy when both CMF and OBV are
    above `cmf_level`/`obv_level` (0.0 in the source); close the position
    when both drop below. Two adaptations from the source, both
    interpretation decisions — check against intent before trusting
    results:
      - **OBV windowed, not cumulative-since-inception** (see
        `indicators.on_balance_volume`'s own docstring for why): the
        source's raw backtrader OBV crossing a fixed "0" is only meaningful
        relative to wherever its running sum happened to start at the
        beginning of that specific data feed — not comparable across the
        different windows this project's walk-forward/quarter-split checks
        require. Windowing it makes the 0-crossing a stable "more up-volume
        than down-volume in the last N days" signal instead.
      - **Symmetric short side added** (the source is long-only): this
        project's own extension for consistency with every other strategy
        here, the same caveat already applied to `probe_ibs.py`'s short
        side — not itself source-backed, flag results on it accordingly.

    No natural structural stop in the source rule (same situation RSI-2 and
    Squeeze were in), so the initial stop is `stop_atr_multiple` x ATR.
    `max_hold_days` is this file's now-standard bounded-holding time-stop
    (same `_days_in_trade`-via-`push()` convention as SqueezeMomentumStrategy).

    Known applicability limit, not a bug: yfinance reports zero/unreliable
    volume for INDEX symbols (`^NSEI`, `^NSEBANK`) since an index itself
    has no traded volume — `chaikin_money_flow` returns None in that case
    (see its docstring) and this strategy simply never enters. Only test
    this against real instruments (stocks, futures), not indices.
    """
    cmf_period: int = 20
    obv_period: int = 20
    cmf_level: float = 0.0
    obv_level: float = 0.0
    stop_atr_multiple: float = 2.0
    atr_period: int = 14
    max_hold_days: int = 20

    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)
    _closes: list[float] = field(default_factory=list, init=False)
    _volumes: list[float] = field(default_factory=list, init=False)
    _in_trade: bool = field(default=False, init=False)
    _days_in_trade: int = field(default=0, init=False)

    def reset(self) -> None:
        self._highs = []
        self._lows = []
        self._closes = []
        self._volumes = []
        self._in_trade = False
        self._days_in_trade = 0

    def push(self, bar: dict) -> None:
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])
        self._closes.append(bar["close"])
        self._volumes.append(bar["volume"])
        if self._in_trade:
            self._days_in_trade += 1

    def _window(self, n: int) -> list[dict]:
        return [{"high": h, "low": l, "close": c, "volume": v} for h, l, c, v in
                zip(self._highs[-n:], self._lows[-n:], self._closes[-n:], self._volumes[-n:])]

    def current_atr(self) -> float | None:
        n = self.atr_period + 1
        if len(self._closes) < n:
            return None
        window = [{"high": h, "low": l, "close": c} for h, l, c in
                  zip(self._highs[-n:], self._lows[-n:], self._closes[-n:])]
        return average_true_range(window, period=self.atr_period)

    def _cmf_obv(self) -> tuple[float | None, float | None]:
        """(cmf, obv) from the days already pushed — today NOT included
        (called from check_entry/check_exit BEFORE push() runs for today),
        same no-lookahead convention as Donchian/Squeeze."""
        cmf = chaikin_money_flow(self._window(self.cmf_period), period=self.cmf_period)
        obv = on_balance_volume(self._window(self.obv_period + 1), period=self.obv_period)
        return cmf, obv

    def check_entry(self, close: float) -> Signal | None:
        cmf, obv = self._cmf_obv()
        atr = self.current_atr()
        if cmf is None or obv is None or atr is None or atr <= 0:
            return None
        if cmf > self.cmf_level and obv > self.obv_level:
            self._in_trade = True
            self._days_in_trade = 0
            return Signal(Side.LONG, close, close - self.stop_atr_multiple * atr,
                          f"CMF={cmf:.3f} OBV={obv:.0f} both above {self.cmf_level}/{self.obv_level}")
        if cmf < -self.cmf_level and obv < -self.obv_level:
            self._in_trade = True
            self._days_in_trade = 0
            return Signal(Side.SHORT, close, close + self.stop_atr_multiple * atr,
                          f"CMF={cmf:.3f} OBV={obv:.0f} both below -{self.cmf_level}/-{self.obv_level}")
        return None

    def check_exit(self, close: float, side: Side) -> bool:
        cmf, obv = self._cmf_obv()
        timed_out = self._days_in_trade >= self.max_hold_days
        if cmf is None or obv is None:
            signal_exit = False
        elif side == Side.LONG:
            signal_exit = cmf < self.cmf_level and obv < self.obv_level
        else:
            signal_exit = cmf > -self.cmf_level and obv > -self.obv_level
        if signal_exit or timed_out:
            self._in_trade = False
            return True
        return False


@dataclass
class TurtleSoupStrategy:
    """"Turtle Soup" — Linda Raschke's failed-breakout fade (Raschke &
    Connors, "Street Smarts"), the exact counter-trend INVERSE of
    DonchianBreakoutStrategy above: bet that a new N-day extreme is a
    stop-hunting false breakout that reverts, rather than that it's the
    start of a real trend. Genuinely different premise from every other
    strategy in this file — it fades precisely the signal Donchian and
    SuperTrend trade WITH, the first strategy here built specifically to
    exploit failed continuation instead of assuming continuation.

    Original rule: price makes a new `channel_period`-day extreme (the
    classic Turtle system's own entry trigger) but fails to hold it — the
    very next bar closes back inside the prior range. That failure is the
    signal: buy a failed new low, sell a failed new high, stop just beyond
    the failed extreme, hold only a few days (Raschke's own rule is a very
    short-term trade, unlike this file's other trend/momentum strategies).

    Checked one bar in arrears using only push()'d history (no lookahead):
    at entry-check time, `self._highs/_lows/_closes` hold everything
    through YESTERDAY (today not yet pushed — same convention as every
    other strategy here). `prior_high`/`prior_low` are the channel excluding
    yesterday itself (`highest(self._highs[:-1], channel_period)`) — the
    range yesterday's close needed to break to count as "a new extreme."
    If yesterday's close broke that range and TODAY's close (the `close`
    argument) reverses back inside it, that's the failed breakout being
    faded.

    stop_buffer_atr_mult / target_r_multiple / max_hold_days follow the
    same ATR-generalization and fixed-R-target convention already applied
    to ThreeBarBreakoutStrategy, for the same reason (no natural moving
    exit condition exists for a fixed pattern-failure signal) — default
    max_hold_days is shorter (5, not 20) to match Raschke's own short
    holding period rather than this file's other strategies' longer ones.
    """
    channel_period: int = 20
    stop_buffer_atr_mult: float = 0.5
    target_r_multiple: float = 1.5
    max_hold_days: int = 5
    atr_period: int = 14

    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)
    _closes: list[float] = field(default_factory=list, init=False)
    _target: float | None = field(default=None, init=False)
    _days_in_trade: int = field(default=0, init=False)

    def reset(self) -> None:
        self._highs = []
        self._lows = []
        self._closes = []
        self._target = None
        self._days_in_trade = 0

    def push(self, bar: dict) -> None:
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])
        self._closes.append(bar["close"])
        if self._target is not None:
            self._days_in_trade += 1

    def _current_atr(self) -> float | None:
        n = self.atr_period + 1
        if len(self._closes) < n:
            return None
        window = [{"high": h, "low": l, "close": c} for h, l, c in
                  zip(self._highs[-n:], self._lows[-n:], self._closes[-n:])]
        return average_true_range(window, period=self.atr_period)

    def current_atr(self) -> float | None:
        """Public wrapper, same purpose as ThreeBarBreakoutStrategy's."""
        return self._current_atr()

    def check_entry(self, close: float) -> Signal | None:
        if len(self._closes) < self.channel_period + 1:
            return None
        atr = self._current_atr()
        if atr is None or atr <= 0:
            return None

        prior_high = highest(self._highs[:-1], self.channel_period)
        prior_low = lowest(self._lows[:-1], self.channel_period)
        if prior_high is None or prior_low is None:
            return None
        yesterday_close = self._closes[-1]

        if yesterday_close < prior_low and close > prior_low:
            stop = self._lows[-1] - self.stop_buffer_atr_mult * atr
            risk = close - stop
            self._target = close + self.target_r_multiple * risk
            self._days_in_trade = 0
            return Signal(Side.LONG, close, stop, f"failed {self.channel_period}-day low, faded")

        if yesterday_close > prior_high and close < prior_high:
            stop = self._highs[-1] + self.stop_buffer_atr_mult * atr
            risk = stop - close
            self._target = close - self.target_r_multiple * risk
            self._days_in_trade = 0
            return Signal(Side.SHORT, close, stop, f"failed {self.channel_period}-day high, faded")

        return None

    def check_exit(self, close: float, side: Side) -> bool:
        hit_target = (close >= self._target) if side == Side.LONG else (close <= self._target)
        timed_out = self._days_in_trade >= self.max_hold_days
        if hit_target or timed_out:
            self._target = None
            return True
        return False


@dataclass
class MACDStrategy:
    """MACD (Moving Average Convergence/Divergence) crossover — Gerald
    Appel's original indicator, one of the most widely used technical
    signals that exists, and notably the first MOMENTUM-OF-A-TREND
    mechanism in this file rather than a price-channel breakout (Donchian,
    SuperTrend), a level-based oscillator (RSI-2), a fixed pattern
    (3-bar breakout, Turtle Soup), or a volatility-state signal (Squeeze).
    Rule: macd_line = EMA(fast) - EMA(slow) of closes; signal_line =
    EMA(signal_period) of the macd_line itself (an EMA of an EMA-derived
    series). Buy when macd_line crosses above signal_line, sell/short when
    it crosses below. Classic defaults 12/26/9.

    Unlike every other indicator in this file (rsi/average_true_range/
    stdev/linreg), which recompute fresh from a bounded trailing window
    every call, an EMA's defining property is that older bars never fully
    drop out — so recomputing it from a window each call would silently be
    a DIFFERENT indicator. This strategy therefore keeps running EMA state
    (`_ema_fast`, `_ema_slow`, `_macd_ema`) updated once per push() via
    indicators.ema_update(), rather than the windowed-recompute style used
    elsewhere in this file.

    check_entry/check_exit both need TWO consecutive points (yesterday's
    macd-vs-signal relationship, today's) to detect a crossover, but
    push() for today hasn't run yet when they're called (same no-lookahead
    convention as everywhere else — self._ema_fast/_ema_slow/_macd_ema
    hold state as of the end of YESTERDAY's push). `_project(close)`
    computes what today's macd/signal WOULD be via ema_update(), without
    mutating any stored state — a pure "what if" projection, matching
    ConnorsRSI2Strategy's convention of folding today's own close into its
    oscillator while every other rolling window stays lookahead-free.

    No natural structural stop in the source rule (same situation RSI-2,
    Squeeze, and volume were in), so the initial stop is
    `stop_atr_multiple` x ATR. `max_hold_days` is this file's now-standard
    bounded-holding time-stop, since a crossover can be very slow to flip
    back in a strongly trending market.
    """
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    stop_atr_multiple: float = 2.0
    atr_period: int = 14
    max_hold_days: int = 20

    _highs: list[float] = field(default_factory=list, init=False)
    _lows: list[float] = field(default_factory=list, init=False)
    _closes: list[float] = field(default_factory=list, init=False)
    _ema_fast: float | None = field(default=None, init=False)
    _ema_slow: float | None = field(default=None, init=False)
    _macd_ema: float | None = field(default=None, init=False)
    _macd_seed: list[float] = field(default_factory=list, init=False)
    _in_trade: bool = field(default=False, init=False)
    _days_in_trade: int = field(default=0, init=False)

    def reset(self) -> None:
        self._highs = []
        self._lows = []
        self._closes = []
        self._ema_fast = None
        self._ema_slow = None
        self._macd_ema = None
        self._macd_seed = []
        self._in_trade = False
        self._days_in_trade = 0

    def push(self, bar: dict) -> None:
        self._highs.append(bar["high"])
        self._lows.append(bar["low"])
        self._closes.append(bar["close"])
        close = bar["close"]
        n = len(self._closes)

        if self._ema_fast is None:
            if n == self.fast_period:
                self._ema_fast = sum(self._closes[-self.fast_period:]) / self.fast_period
        else:
            self._ema_fast = ema_update(self._ema_fast, close, self.fast_period)

        if self._ema_slow is None:
            if n == self.slow_period:
                self._ema_slow = sum(self._closes[-self.slow_period:]) / self.slow_period
        else:
            self._ema_slow = ema_update(self._ema_slow, close, self.slow_period)

        if self._ema_fast is not None and self._ema_slow is not None:
            macd = self._ema_fast - self._ema_slow
            if self._macd_ema is None:
                self._macd_seed.append(macd)
                if len(self._macd_seed) == self.signal_period:
                    self._macd_ema = sum(self._macd_seed) / self.signal_period
            else:
                self._macd_ema = ema_update(self._macd_ema, macd, self.signal_period)

        if self._in_trade:
            self._days_in_trade += 1

    def _current_atr(self) -> float | None:
        n = self.atr_period + 1
        if len(self._closes) < n:
            return None
        window = [{"high": h, "low": l, "close": c} for h, l, c in
                  zip(self._highs[-n:], self._lows[-n:], self._closes[-n:])]
        return average_true_range(window, period=self.atr_period)

    def current_atr(self) -> float | None:
        """Public wrapper, same purpose as the other strategies' own."""
        return self._current_atr()

    def _project(self, close: float) -> tuple[float, float] | tuple[None, None]:
        """What today's (macd, signal) WOULD be if `close` were pushed,
        without mutating any stored state - see class docstring."""
        if self._ema_fast is None or self._ema_slow is None or self._macd_ema is None:
            return None, None
        today_fast = ema_update(self._ema_fast, close, self.fast_period)
        today_slow = ema_update(self._ema_slow, close, self.slow_period)
        today_macd = today_fast - today_slow
        today_signal = ema_update(self._macd_ema, today_macd, self.signal_period)
        return today_macd, today_signal

    def check_entry(self, close: float) -> Signal | None:
        if self._ema_fast is None or self._ema_slow is None or self._macd_ema is None:
            return None
        atr = self._current_atr()
        if atr is None or atr <= 0:
            return None

        yesterday_macd = self._ema_fast - self._ema_slow
        yesterday_signal = self._macd_ema
        today_macd, today_signal = self._project(close)

        if yesterday_macd <= yesterday_signal and today_macd > today_signal:
            self._in_trade = True
            self._days_in_trade = 0
            return Signal(Side.LONG, close, close - self.stop_atr_multiple * atr,
                          "MACD crossed above signal")
        if yesterday_macd >= yesterday_signal and today_macd < today_signal:
            self._in_trade = True
            self._days_in_trade = 0
            return Signal(Side.SHORT, close, close + self.stop_atr_multiple * atr,
                          "MACD crossed below signal")
        return None

    def check_exit(self, close: float, side: Side) -> bool:
        timed_out = self._days_in_trade >= self.max_hold_days
        yesterday_macd = self._ema_fast - self._ema_slow
        yesterday_signal = self._macd_ema
        today_macd, today_signal = self._project(close)
        if today_macd is None:
            signal_exit = False
        elif side == Side.LONG:
            signal_exit = yesterday_macd >= yesterday_signal and today_macd < today_signal
        else:
            signal_exit = yesterday_macd <= yesterday_signal and today_macd > today_signal
        if signal_exit or timed_out:
            self._in_trade = False
            return True
        return False
