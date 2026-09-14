# CLAUDE.md

Guidance for Claude Code sessions working in this repo.

## What this is

An intraday reversal strategy ("Highest Open / Lowest Open", source: a
ForexFactory thread — full rule text is in `strategy.py`'s module docstring)
wired to Zerodha's Kite Connect API. No framework, no build step — plain
Python, `requirements.txt` only.

## Setup / run

```
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # fill in KITE_API_KEY / KITE_API_SECRET, see README.md for the daily access-token dance
pytest -v               # network-free, safe to run anytime
python backtest.py --token <instrument_token> --from YYYY-MM-DD --to YYYY-MM-DD
python run_live.py --token <instrument_token> --symbol <symbol> --exchange <NSE|NFO>   # paper mode by default
```

## Architecture: strict separation of pure logic from I/O

- `strategy.py` and `risk.py` are **pure** — no imports of `kiteconnect`,
  no datetime.now(), no file/network I/O. This is deliberate and mirrors
  the pattern already established in `~/repos/scraping-practice`: keep
  "decide what to do given this data" separable from "go get the data" so
  it's testable without mocking a broker. `tests/` runs against plain
  floats/dataclasses, no fixtures needed, no network ever.
- `kite_client.py` / `data.py` / `kite_ticker.py` are the only files that
  import `kiteconnect`.
- `kite_ticker.py`: real-time price via Kite's own `KiteTicker` class, used
  by `run_live.py --stream` instead of polling. Original implementation
  against KiteTicker's documented API — NOT copied from any third-party
  project. (A well-known open-source algo platform, OpenAlgo, is
  AGPL-3.0-licensed and reimplements the raw WebSocket protocol itself for
  reasons specific to its hosted, multi-user, 1800+-symbol deployment;
  copying that code would obligate this repo to AGPL's copyleft terms, and
  none of its scaling reasons apply to a personal single-instrument bot
  anyway — KiteTicker directly is the right-sized tool here.) `run_live.py`'s
  `Trader` class holds the shared strategy/broker/risk state behind a lock,
  since `--stream` calls `on_ltp` from KiteTicker's background thread while
  candle refreshes happen on the main thread.
- `data_yfinance.py` is a free alternative data source (Yahoo Finance, no
  API key) — used by `backtest.py --source yfinance`, the default, since
  the free Kite "Personal" app tier has no historical-data access at all.
  It returns the exact same candle shape as `data.py`'s Kite wrapper
  (`{date, open, high, low, close, volume}` dicts), so `backtest.py`'s
  `simulate()` function is fully source-agnostic and doesn't know or care
  which one fed it. `run_backtest_yfinance()` / `run_backtest_kite()` are
  the only two places that import a specific source.
- `paper_broker.py` has zero dependency on Kite — it's a pure simulator and
  is the **default** broker in both `backtest.py` and `run_live.py`. Real
  orders only ever come from `kite_client.place_order()`, called from
  exactly one place in `run_live.py`, gated behind `--live` AND
  `MONEYMAKING_LIVE=true`.

## Interpretation decisions baked into strategy.py (verify against intent)

The source is a forum post in prose, not a spec, so some choices were made
turning it into code — check these against what's actually wanted before
trusting backtest results:

- **One-shot-per-level** (`one_shot_per_level=True` default): a given
  highest/lowest-open value only triggers one entry. A later H1 bar that
  pushes the line further resets it (new level, can fire again). The
  source doesn't explicitly address repeat entries at an unchanged level;
  this reads the thread's emphasis on "wait" as implying no re-churn on
  the same line.
- **Units generalised, not forex pips**: the source's "+5 -> breakeven+1,
  +10 -> trail" is in forex pips. `TrailingStopManager`'s thresholds are
  plain price-unit floats — pick numbers that make sense for whatever
  instrument this points at (an index trading in hundreds of points needs
  very different thresholds than a stock trading in tens of rupees).
- **M15 filter is opt-in** (`m15_filter=False` default) — it was a later
  addition to the source thread aimed at traders entering too early, not
  part of the original rule.
- **Stop-loss = day's high/low *at the moment of entry***, not a
  continuously-updating "current daily high/low" that could move against
  an open position before the trailing-stop logic takes over. Re-read the
  source's "stop loss is the current daily high or current daily low" if
  this diverges from intent — it's arguably ambiguous which moment is meant.

## Verified against real data (not just unit tests)

`python backtest.py --source yfinance --symbol RELIANCE.NS --period 30d` and
`--symbol '^NSEI' --period 60d` were both actually run against live Yahoo
Finance data during development (not just imagined to work) — confirmed the
full pipeline executes end-to-end and produces plausible trade logs. This
surfaced a real finding, not a hypothetical one: the source thread's +5/+10
trailing-stop thresholds are forex pips and are essentially noise on the
Nifty index (24,000+ points) — 146 trades over 60 days with defaults, vs 77
with `--breakeven-trigger 30 --trail-trigger 60`. Don't assume the defaults
are reasonable for whatever instrument is used next; check trade frequency
in the output.

## Known gaps (see README's "Known limitations" for the user-facing version)

- `backtest.py` feeds each minute candle's OHLC in open→high→low→close
  order as an approximation of the true tick path — Kite's historical API
  doesn't expose ticks. Don't treat backtest P&L as more precise than that
  approximation allows.
- `run_live.py` polls every 15s rather than streaming via `KiteTicker`.
  Fine for a first cut on a liquid, not-hyper-fast-moving instrument; an
  upgrade path if entries need to be tighter to the true first touch.
- No automated daily Kite login — deliberately not built (Zerodha's 2FA
  login isn't meant to be scripted around by a third-party tool). The
  manual `login_url()` → `generate_access_token()` dance in README.md is
  the intended daily routine.

## Safety rails already in place — don't loosen without being asked

- `run_live.py` requires **both** `--live` and `MONEYMAKING_LIVE=true` to
  place a real order — two independent opt-ins on purpose.
- `RiskManager.trading_halted()` combines TWO breakers: a same-day loss
  limit (`max_daily_loss_pct`, resets via `reset_day()`) AND a cumulative
  drawdown-from-peak limit (`max_drawdown_pct`, does **not** reset via
  `reset_day()` — only `reset_capital()`, which must be called
  deliberately, never automatically from a runner). Both are checked
  before every new entry in both `backtest.py` and `run_live.py`.
- `PaperBroker` is the default broker everywhere; nothing routes to
  `kite_client.place_order()` except the one gated call site in
  `run_live.py`.

## Backtest rigor tools (added after a systematic 9-instrument check)

Testing this strategy across 9 instruments x 60 days x {with, without}
M15-filter (18 backtests, ~970 trades) found **no consistent edge** —
aggregate P&L was negative both ways, and every single-period "win" flipped
sign under either perturbation. Two tools exist specifically to catch this
kind of false positive before trusting a result:

- **`backtest.py --walk-forward`**: splits the sample into two independent
  halves (own fresh capital/risk state each) and reports both. A real edge
  should look broadly similar on both. Every previously "profitable"
  single-period result checked this way (INFY.NS, HDFCBANK.NS, ITC.NS) came
  back **inconsistent** across halves — profitable on one, losing on the
  other. Treat any new single-period result the same way before trusting it.
- **`RiskManager.drawdown_from_peak_pct` / `.drawdown_halted`**: surfaced
  directly in `backtest.py`'s per-run report (`_report()`). A backtest can
  show misleadingly large P&L swings if drawdown isn't checked alongside
  the raw number — see HDFCBANK.NS's in-sample half: +36,657 P&L *and*
  drawdown-halted, because it gave back >10% of capital from a much higher
  peak before the run ended.

Do not report a bare P&L number as "the result" going forward without also
running `--walk-forward` and checking `drawdown_halted` — a single
whole-period P&L number has already been shown to be misleading on this
exact codebase.

**Walk-forward blind spot found**: a same-sign match across the two halves
is not automatically reassuring — if BOTH halves independently hit the
drawdown breaker, they'll show near-identical, near-floor P&L and register
as "CONSISTENT" even though that's an artifact of the cap truncating both
runs, not two runs agreeing on a real edge (seen testing the breakout
variant on oil: both halves halted at ~10.5% drawdown with nearly identical
P&L). Check `drawdown_halted` on BOTH halves before trusting a
"CONSISTENT" verdict — if either tripped, the match is hollow.

## Systematic edge search: 216 configs tested, none survived

Swept 18 instruments x 3 proportional-threshold scales x 2 variants x 2
M15-filter settings (216 walk-forward runs) looking for a config where both
halves are profitable with no drawdown-halt. Only 2/216 passed that screen
(`^NSEI` and `SI=F`, both reversal/no-filter/tight 0.2%-of-price thresholds)
— already a suspicious hit rate, close to what pure chance produces from
216 comparisons (classic multiple-comparisons/data-mining bias). Both died
under further scrutiny:

- **Parameter perturbation** (Davey's robustness check — nudge the
  threshold slightly, a real edge degrades gradually): SI=F went
  +22,310 at pct=0.002 to -6,609 (drawdown-halted) at pct=0.003 — a cliff,
  not a gradient. ^NSEI was worse: +16k, +10k, **-11k (halted)**, +23k
  across five nearby threshold values with no smooth pattern at all. Both
  are curve-fit noise, not real patterns.
- **Transaction costs** (previously unmodeled — see `commission_per_trade`
  below): SI=F traded 139-384 times per backtest half. Applying a
  conservative ~$4/round-trip cost estimate turned every single one of its
  "profitable" configs net-negative, including the best one
  (+22,310 gross -> -1,269 net). High trade-count results are exactly the
  ones a zero-cost backtest will most badly overstate.

Conclusion: no edge found in this search, and — importantly — the search
methodology itself (walk-forward + perturbation + cost modeling) is what
caught it. A less careful sweep would have reported SI=F as a working
Highest-Open/Lowest-Open scalping system. Don't skip these three checks
(walk-forward, perturbation, costs) on any future candidate, and don't
trust a "found it" from a single 2-way split alone.

## Transaction costs — previously completely unmodeled

`PaperBroker.commission_per_trade` (default 0.0, backward compatible) is a
flat cost deducted per round-trip trade in `close()`; the trade log now
carries both `gross_pnl` and `pnl` (net) so the two are always visible
separately. `backtest.py --commission-per-trade <value>` wires it through
the CLI. **Always re-run a promising result with a realistic non-zero value
before trusting it** — the SI=F case above is the concrete reason this
exists, not a hypothetical.

## Daily-bar trend-following: a second, genuinely different strategy family

`daily_strategy.py` (`DonchianBreakoutStrategy`) + `backtest_daily.py` are a
second family, added after the intraday Highest-Open/Lowest-Open family (2
variants x 18 instruments) was exhaustively falsified. Different mechanism
(N-day high/low channel breakout — the "Turtle Trading" system's entry
rule), different timeframe (daily bars, not H1/M15/tick), and critically
different data constraint: daily data goes back **years** via yfinance, not
capped at 60 days like intraday — escaping the single-window limitation
that was the biggest methodological weakness of every earlier test.

**A real bug was found and fixed before any result could be trusted**:
`simulate_daily()` originally called `PaperBroker.enter()` without
overriding its default trailing-stop thresholds (breakeven at +5, trail at
+10 — tuned for forex pips). On ETH-USD (price in the hundreds/thousands),
every position closed for a manufactured, mechanically-exact tiny profit
within hours of entry, producing a fake **100% win rate** over 102 trades —
caught only because that number is essentially impossible for a real
trend system and demanded investigation. Root cause: `strategy.py`'s
`TrailingStopManager` narrows the stop to a fixed point distance from
entry regardless of the instrument's actual volatility, which is the
opposite of what a trend-follower needs (let the winner run). Fix:
`DonchianBreakoutStrategy` now has its own `check_exit()` using a SHORTER
second channel (`exit_period`, default `entry_period // 2` — the classic
Turtle dual-channel shape), and `simulate_daily()` passes
`breakeven_trigger=trail_trigger=float("inf")` into `PaperBroker.enter()`
to fully disable `TrailingStopManager`'s early-exit behavior, leaving only
the original structural stop as a hard backstop. **Any future strategy
that holds positions for more than a few bars must either pass
appropriately-scaled trailing thresholds or disable `TrailingStopManager`
this same way — its defaults are not a safe no-op.**

**Systematic finding, after the fix** (18 instruments x 2 classic Turtle
periods [20, 55] x walk-forward x realistic ~5bps commission x parameter
perturbation x quarter-split): the mechanism itself is far more robust than
the Highest-Open/Lowest-Open family ever was — BTC-USD and ETH-USD show
**smooth, monotonic-ish, all-positive performance across every tested
entry_period from 10 to 120 days** (no cliffs, no sign flips — real
Davey-style robustness, a first for this project). But two things prevent
calling this a usable edge:

1. **Crypto isn't tradable via Kite at all** (NSE/BSE/MCX only) — BTC/ETH
   results are scientifically interesting (the mechanism isn't broken) but
   not actionable through this project's actual broker integration.
2. **Quarter-splitting BTC/ETH reveals the "edge" is a decaying
   bull-market artifact, not a repeatable pattern**: BTC's 10-year P&L is
   concentrated almost entirely in Q1-Q2 (2016-2021, +10.5%/+22.1%
   returns, the era BTC rose ~100x), decaying to Q3 +3.9% and **Q4
   (2024-2026, the most recent, most relevant period) essentially flat at
   -0.05%**. Breaking down by side: profit came almost entirely from LONGS
   during those two historic bull quarters; SHORTS were flat-to-negative
   throughout every quarter. This is "a trend-follower captured much of
   crypto's unprecedented historic rise," not "a validated repeatable
   edge" — and it shows no meaningful edge in the window closest to now.

The Kite-tradable candidates (`GC=F` gold, `CL=F` oil — both accessible via
MCX) are considerably weaker: gold's full-10-year perturbation sweep looks
mostly positive, but quarter-splitting shows 2 of 4 quarters negative with
tiny magnitudes (fractions of a percent per 2.5-year quarter) — noise, not
signal, once regime-split. Oil's perturbation sweep is mixed/erratic at
short entry_periods. Neither survives the same bar BTC/ETH's mechanism
cleared.

**Net verdict**: the Donchian/trend-following mechanism is real progress —
first strategy family this session that didn't show cliff-edge parameter
sensitivity — but nothing tested is both (a) tradable through this
project's actual Kite integration and (b) showing a current, non-decayed
edge. Next step if continuing this line: test more Kite-tradable trending
instruments (index futures, currency pairs, other MCX commodities) with
the same full rigor (perturbation + quarter-split, not just walk-forward
halves — halves alone would have called BTC/ETH "done" without surfacing
the decay).

## Third strategy: Connors RSI-2 mean reversion (`ConnorsRSI2Strategy`)

Added after Donchian breakout, and genuinely different again: SHORT-TERM
mean reversion filtered by a LONG-TERM trend, not trend-following extremes
or crossovers. The standard, widely-published rules (Connors & Alvarez,
"Short Term Trading Strategies That Work"): 200-day SMA trend filter, enter
on RSI(2) < 5 (long, above the filter) or > 95 (short, below it), exit on a
close back through the 5-day SMA or the trend filter flipping. Added
`indicators.sma()` / `indicators.rsi()` (windowed Wilder smoothing,
consistent with `average_true_range`'s own windowed style) to support it.
No natural structural stop exists for this rule (unlike Donchian's own
channel), so the initial stop is `stop_atr_multiple` x ATR — a deliberate
addition on top of Connors' original publication, necessary because this
repo's risk sizing requires a numeric stop on every `Signal`.

**A subtle lookahead trap avoided**: RSI(2) is specifically about very
recent price action, so `check_entry()` computes it from
`self._closes + [close]` (including TODAY's close) even though the trend
SMA and ATR intentionally use only the window BEFORE today (matching
Donchian's no-lookahead convention). Using `self._closes` alone for RSI
would make "detect today's oversold dip" actually mean "detect
yesterday's" — one day stale, and specifically damaging for a 2-period
window where one day is half the whole lookback.

**A second, more severe bug found and fixed while testing this** (see
`backtest_daily.py`'s `simulate_daily()`): it never called
`risk.reset_day()`. `RiskManager`'s "daily" loss breaker (default 2% of
capital) is designed to reset every day via that call — `backtest.py`'s
intraday loop does this correctly once per calendar day. But
`simulate_daily()` runs one bar per day and never called it at all, so
`_realized_pnl_today` silently accumulated for the ENTIRE multi-year
backtest instead of resetting daily, turning "2% daily loss limit" into "2%
cumulative-loss-since-inception limit, ever" — trading permanently stopped
after roughly 4 losing trades (at the default 0.5% risk-per-trade) and
stayed stopped for however many years of data remained, with no error or
warning. Caught only while investigating why raising `risk_per_trade_pct`
above ~1.5% made trade counts collapse to 2-5 regardless of instrument —
a symptom that demanded explanation rather than being written off as "the
strategy just doesn't like bigger size." Fixed with one line:
`risk.reset_day()` after each bar. A regression test
(`test_simulate_daily_resets_the_daily_loss_breaker_each_bar`) builds 8
losing round-trips specifically to prove trading continues past the point
the bug would have silently killed it. **Re-verified impact**: the
Donchian BTC-USD/ETH-USD numbers reported earlier were unaffected (their
few-large-winners profile never crossed the 2% cumulative threshold); the
8 RSI-2 candidates below were also unaffected; BTC-USD and GC=F's
(non-candidate, already-negative) Donchian numbers did shift once
re-verified.

**Systematic finding**: 14 instruments, walk-forward, ~5bps commission,
proper capital scaling — an 8/14 hit rate (57%), by far the highest of any
strategy tested this session (Donchian: 32%, intraday reversal: 1%), and
critically on REAL Kite-tradable instruments this time (`^NSEI`, `^NSEBANK`,
`RELIANCE.NS`, `TCS.NS`, `INFY.NS`, `HDFCBANK.NS`, `ITC.NS`, `CL=F`), not
just crypto. Parameter perturbation on `^NSEI` is smooth on `trend_period`
and `stop_atr_multiple` (all-positive across the tested range, no cliffs),
and the RSI-threshold perturbation shows a coherent, monotonic story rather
than noise: extreme thresholds (2/98, 5/95, 10/90) are all positive,
moderate ones (15/85 and looser) all flip negative — which matches
Connors' own thesis that only genuinely extreme RSI(2) readings capture
real capitulation, not ordinary "overbought/oversold." This is the most
theoretically coherent, sign-robust result of the whole session.

**But the magnitude kills it as a usable strategy, at any reasonable risk
setting**: at the standard 0.5% risk-per-trade, `^NSEI`'s 10-year return
is 0.40% total — **0.04%/year**. Every Kite-tradable candidate is between
0.04%/year (ITC.NS) and 0.25%/year (TCS.NS). Checked directly whether more
aggressive position sizing fixes this (`--risk-per-trade-pct`, tested 0.5
through 10%): trade count stays fixed (64) at every level since sizing
doesn't change WHICH signals fire, and return/drawdown both scale
proportionally with risk — at 10% risk per trade (reckless for real
money), `^NSEI` reaches only **0.83%/year with 19% drawdown**. This is not
a sizing problem to be solved with leverage; the underlying per-trade edge
is real (statistically) but too thin to be worth the operational risk at
any sizing.

**Net verdict**: the most credible finding of the entire session in terms
of methodology (real theoretical grounding, smooth parameter sensitivity,
high hit rate, actually Kite-tradable) — and still not tradable, because
the edge is economically negligible after being sized honestly. This is a
different failure mode than everything before it (which failed on
robustness/consistency); RSI-2 failed on magnitude despite being robust.
Worth remembering when evaluating the next candidate: passing every
rigor check is necessary but not sufficient — always compute annualized
return on realistic capital before calling anything "found."

## Donchian extended to FX pairs and more MCX commodities: also no survivors

Followed through on the "next step" noted above: tested the instruments the
BTC/ETH/gold/oil sweep hadn't covered yet — 4 Kite-tradable currency pairs
(`USDINR=X`, `EURINR=X`, `GBPINR=X`, `JPYINR=X`) and 4 more MCX commodities
(`NG=F` natural gas, `HG=F` copper, `SI=F` silver, `PL=F` platinum) — at both
classic Turtle periods (`--entry-period 20` and `55`), 10y history, ~20-unit
commission per round trip, walk-forward halves. 16 configs total.

**Zero passed the first screen** (both halves profitable, no drawdown-halt) —
didn't even reach the perturbation/quarter-split stage, since walk-forward
alone eliminated every one:
- 20-day: all 8 negative on both halves except `USDINR=X`, which flipped sign
  (+3,565 in-sample / -3,648 out-of-sample) — the same "looks good on one
  half, reverses on the other" pattern already seen and distrusted elsewhere
  in this file.
- 55-day: mostly negative-both-halves again; the exceptions are all sign
  flips with small magnitudes (`USDINR=X` +520/-1,865, `EURINR=X` -119/+278,
  `HG=F` +405/-2,786) — same pattern, not a real edge.

**Net verdict**: extends the existing finding rather than changing it. Across
this whole project (intraday reversal: 1/216 configs survived screening and
died under perturbation+costs; Donchian daily trend: BTC/ETH cleared
robustness but turned out to be decaying bull-market beta, not tradable via
Kite anyway; gold/oil were weak; RSI-2 passed every rigor check but the edge
was too thin to matter) — FX pairs and the remaining MCX commodities add a
fourth negative result to the Donchian family specifically. No further
instruments obviously remain to try under this same mechanism; a genuinely
different next step (different mechanism entirely, not another instrument
swap) would be needed to keep searching.

## Two strategy variants exist — same data pipeline, opposite premise

- `HighLowOpenStrategy` ("reversal", default): the source thread's rule —
  fade a breakout back through the line.
- `HighLowOpenBreakoutStrategy` ("breakout"): trend-following inverse —
  trade WITH the breakout, stop-loss at the line itself. Built to test
  whether a trending instrument (oil) would suit a trend-follower better
  than the reversal rule. It didn't: on oil, 60d, the breakout variant
  went 0-for-21 (0% win rate) and lost 3x more than the reversal variant
  (-$10,499 vs -$3,500) before its own drawdown breaker halted it. Read as:
  removing the reversal rule's "wait for price to return through the line"
  step removes the one thing filtering out breakout fakeouts/whipsaws —
  don't assume "trend-following variant" is an automatic improvement on a
  trending instrument without testing it, as this one didn't hold up.
- Select via `backtest.py --variant {reversal,breakout}`; both share
  `simulate()`, `walk_forward()`, and the same CLI flags.
