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

## Fourth strategy explored: cross-sectional momentum rotation — also decays to nothing recently

Explored per the "genuinely different mechanism" suggestion above, prompted
by a low-capital-focused request: monthly-rebalanced top-N momentum rotation
across a 25-stock NSE large/mid-cap universe (6-month trailing return, 1-month
skip — the standard academic "12-1"-style construction scaled to 6-1), not
yet ported into this repo's pure-strategy architecture (explored as a
standalone probe script, not committed as code here).

**Why this shape looked attractive for a low-capital setup specifically**:
monthly rebalance means ~12 decisions/year, decided after market close using
free EOD data (no Kite Connect subscription needed at all — `data_yfinance.py`
already covers this), with the actual order placed the next morning via
Kite's free Personal tier (order placement only; no paid Connect tier needed
since no live/historical data is pulled from Kite). No need for the machine
to be on during market hours either, unlike every intraday strategy already
in this repo. And unlike everything else tested here, Zerodha equity
**delivery brokerage is zero** — the only real per-trade costs are ~0.2%
STT+stamp on traded value and a flat ~₹16/scrip DP charge on the sell side,
both modeled explicitly.

**Initially the most robust-looking result of the whole project**: full
10-year period beat the `^NSEI` benchmark on both return and drawdown
(14.8% CAGR / -27.4% max DD vs benchmark's 10.5% / -29.3%); walk-forward
halves were BOTH positive and both beat their own half's benchmark
(24.1% vs bench 15.0% in-sample, 10.5% vs bench 5.8% out-of-sample); a
16-config perturbation sweep (top_n in {3,5,7,10} x lookback in
{3,6,9,12} months) was positive in all 16 cells with no cliffs — smoother
than any other sweep run in this project. Realistic low-capital costs
(₹20k-₹300k tested) barely moved the needle: the flat DP fee costs a
₹20k account ~13.5% of starting capital cumulatively over 10 years
(~1.35%/year drag) vs ~0.9% for a ₹300k account, but CAGR stayed
13.5-14.3% net across every capital size tested — the edge, if real,
isn't capital-size-dependent the way flat fees alone would suggest.

**Killed by the same check that caught BTC/ETH's decay**: quarter-splitting
(4 chronological chunks instead of 2 halves) shows Q1-Q3 all strongly
positive (+8.7%, +31.7%, +22.3% CAGR) but **Q4 (2024-03 to 2026-09, the most
recent and most relevant window) is -7.1% CAGR, -16.3% total return** —
negative, not just weaker. Exactly the pattern already seen and distrusted
with Donchian/BTC-USD ("captured a historic run, shows no edge in the window
closest to now") and exactly why this file's Donchian section insists on
quarter-splitting, not just walk-forward halves, before trusting a result.
The 2-way walk-forward split alone would have called this one "done" (both
halves positive) without surfacing the decay — it took the finer split to
catch it, same blind spot already documented above.

**Also flagged, not yet resolved**: the 25-stock universe was hand-picked
using *today's* well-known large/mid-caps, not a point-in-time historical
index membership list. That's a real survivorship-bias risk on top of the
decay finding — a stock is disproportionately likely to be on a "large-cap"
list picked today if it did well over the backtest window, which could
inflate momentum's apparent edge independent of whether the mechanism is
real. Not resolved here for lack of a free point-in-time constituents
source; would need to be fixed before trusting this further even if the
recent-quarter decay weren't already disqualifying on its own.

**Net verdict**: like Donchian/BTC, a mechanism that captured a real
multi-year run (2019-2024 here) but shows no edge — in fact a loss — in the
window closest to now, on top of an unresolved survivorship-bias question.
Low capital changes the cost arithmetic (flat fees matter more
proportionally, delivery being brokerage-free matters a lot) but doesn't
change the core finding: there's still no strategy in this project, of four
tried, showing a current, non-decayed edge. Capital size was never the
blocker for any of the four; the absence of a real current edge was.

## Fifth strategy explored: pairs trading (statistical arbitrage) — magnitude too small

Prompted by the user confirming the machine can stay up continuously, which
removes the operational objection to strategies needing active monitoring.
Explored a mechanism with a genuinely different return source from all four
above: market-neutral mean reversion on the spread between two correlated
stocks (long one leg, short the other), rather than betting on market
direction the way trend/momentum/reversal all do. Rationale: every prior
strategy's fatal flaw was decaying when the directional regime changed
(bull-market decay, momentum crash) — a market-neutral spread has no such
directional exposure in principle. Not yet ported into this repo's
architecture — explored as a standalone probe script.

Method: rolling 60-day OLS hedge ratio (plain numpy lstsq — no statsmodels
dependency added, consistent with this repo's no-framework style) on log
prices, z-score the spread over the same window, enter at \|z\| > 2, exit at
\|z\| < 0.5 or a 20-day max hold. Tested 8 sector pairs (banking: HDFCBANK/
ICICIBANK/KOTAKBANK/AXISBANK/SBIN cross-pairs; IT: TCS/INFY/WIPRO; FMCG:
ITC/HINDUNILVR; cement: ULTRACEMCO/SHREECEM), 10y daily data, realistic
per-leg costs (0.2% STT+stamp, ~₹16 DP charge per leg sold).

**Result: no viable edge, on magnitude rather than sign or consistency**.
Half the pairs were outright negative over 10 years (HDFCBANK/ICICIBANK
-2.2%/yr, TCS/INFY -2.5%/yr, ITC/HINDUNILVR -5.4%/yr with a brutal -52.3% max
drawdown). The best two (HDFCBANK/KOTAKBANK +2.5%/yr, ICICIBANK/AXISBANK
+2.8%/yr) still carry -11.7% and -14.2% max drawdown respectively — a poor
risk-adjusted return, worse than a fixed deposit for the risk taken. A
window/entry-threshold perturbation sweep on those two best pairs found a
genuine cliff: 60-day and 90-day lookback windows cluster around a thin
+0.5% to +3.4%/yr, while the 30-day window is negative across every
threshold tested (-3.4% to -4.6%/yr) — not the smooth, no-cliff robustness
that would justify trusting it, and even the "good" side of the cliff is too
thin to be worth the operational complexity of running two-legged trades.

**Net verdict**: fifth mechanism tried, fifth to fail — this time on
magnitude/risk-adjustment rather than sign-flip or regime-decay. Confirms
uptime was never the real blocker either: it unlocked this mechanism as
operationally feasible to run, but the underlying statistical premise (these
particular pairs mean-revert usefully) just isn't there at a magnitude worth
trading. Across the whole project (intraday reversal, Donchian trend, RSI-2
mean reversion, momentum rotation, pairs trading), every genuinely different
mechanism tried on liquid Kite-tradable NSE instruments has failed for a
different specific reason — noise, decay, thin edge, or poor risk-adjustment
— which is itself a meaningful, if unwelcome, finding about how hard it is to
find exploitable inefficiency in these particular liquid, well-arbitraged
instruments with simple technical/statistical rules.

## Sixth: calendar/microstructure effects — real in the data, unharvestable after costs

Continued the search per "keep testing on historical data" with a category
that doesn't bet on direction, a spread, or momentum at all: known
microstructure/calendar anomalies, on `^NSEI` (and `^INDIAVIX` for the third
one), free yfinance daily data, 10y. All three are illustrative of the same
lesson already learned from the SI=F silver case (`+22,310 gross -> -1,269
net`), just at a more extreme scale.

- **Overnight (close->open) vs intraday (open->close) decomposition**: a
  striking gross result — overnight-only +29.9% CAGR / -26.9% max drawdown /
  10.4% annualized vol, vs intraday-only **-15.0% CAGR / -80.8% max
  drawdown**, vs full buy-and-hold +10.4% CAGR. This overnight-return
  concentration is a real, published effect (documented in US and Indian
  market literature), not a data artifact — but harvesting it means a full
  delivery round trip **every single trading day** (buy at close, sell next
  open), and equity delivery costs 0.2% STT+stamp plus a ~₹16 DP charge per
  round trip. At roughly daily frequency, that compounds to a catastrophic
  net result: a ₹30,000 account backtested this way goes to zero and past it
  (`total return -128.8%`) over the same 9.8 years the gross version made
  +1,262%. Nifty futures have much lower STT (0.05% vs equity delivery's
  0.2%) and no DP charge, so the arithmetic is less brutal there — but one
  Nifty futures lot's margin requirement (roughly ₹1.5-2L) is fundamentally
  incompatible with "low capital," so that route doesn't apply to this
  project's stated constraint either way. **Real anomaly, unharvestable at
  the account sizes this project targets.**
- **Turn-of-month effect** (long the last 2 + first 3 trading days of each
  month, flat otherwise): gross +5.6% CAGR / -21.8% max drawdown (already
  below the +10.4%/-38.4% benchmark on return, though better on drawdown and
  volatility). Net of realistic costs (241 in/out transitions over the
  period, each a delivery round trip) it flips to **-0.7% CAGR** — the
  already-thin gross edge doesn't survive even a couple hundred transitions'
  worth of STT+DP drag.
- **India VIX tactical timing** (long `^NSEI` only when `^INDIAVIX` is above
  its own trailing 252-day 70th percentile): weak even gross, +2.0% CAGR
  against -37.8% max drawdown — worse on both dimensions than plain
  buy-and-hold before any cost is even applied. Didn't bother cost-modeling
  a result already this weak gross.

**Net verdict**: three more genuinely different mechanisms (none of them
directional-trend, mean-reversion-on-price, or spread-convergence — this
time market-structure and calendar-based), and all three fail, this time
specifically illustrating that a real, publicly-documented statistical
regularity (overnight drift) can still be completely unharvestable once
realistic small-account transaction costs are applied at the frequency
required to capture it. This is the same transaction-cost lesson the project
already learned from SI=F, now shown at its most extreme (a strategy that
looks like it 12x's an account gross, and is instead wiped out net).

## Follow-up: filtering the overnight-drift trade doesn't rescue it either

Natural next question after the overnight-drift finding above: the naive
"trade every single night" version died on costs (2,465 round trips), but
maybe a FILTER that only takes the trade on a subset of higher-conviction
nights keeps enough of the edge to survive at a lower trade count. Tested 11
filters on `^NSEI` (after red/green intraday sessions, after big down days,
by realized-vol tercile, by day-of-week) — trade counts ranged from 2,465
(baseline) down to 220 (only after a >1% down intraday session, ~22
trades/year).

**Every single filter still nets negative**, including the most selective
one: after->1% down days, gross CAGR is a thin +1.6% but net is **-4.2%**
even at only ~22 trades/year. The fixed-cost floor per round trip (~0.2%
STT+stamp + a ~₹16 flat DP charge, roughly 0.25-0.3% all-in on a ₹30,000
position) is simply larger than what a typical overnight move is worth at
this capital size — cutting frequency cuts the gross edge and the cost drag
together, and the cost side never falls fast enough relative to trade count
to let net catch up to zero, let alone positive. This isn't a "found the
right filter" problem; it's the underlying per-trade cost structure being
incompatible with capturing a single-day return at this account size, full
stop. Confirms the earlier verdict rather than reopening it — the overnight
anomaly is real but this project's low-capital constraint rules it out
regardless of how selectively it's traded.

## Seventh: options premium selling (weekly Nifty iron condor) — real edge, wrong capital tier

The one mechanism not yet tried because it needs option pricing, not just
spot history: selling a defined-risk weekly Nifty iron condor (short
strangle with wings bought to cap loss) to harvest the volatility risk
premium. Explored as a standalone probe script, not ported into this
repo's architecture.

**Methodology, and its real limitation**: no free/reliable historical NSE
option-chain data source exists, so premiums are synthesized with
Black-Scholes using `^NSEI` spot (real) and `^INDIAVIX` (NSE's own official
implied-vol index, real, not a guess) as a single flat IV input applied to
all four legs. This can't capture volatility skew (real index puts trade
richer than a flat-IV model implies, calls cheaper) or bid-ask spread on
entry, especially on the less-liquid long wings — both bias the result
optimistic relative to what a real fill would achieve. Flagging this
prominently: **this has not been validated against real option-chain
prices**, unlike every other strategy in this file which at minimum uses
real spot OHLC throughout.

**Within that caveat, the statistical result is the strongest and most
robust of anything tried in this project**: full 10y period +20.5% CAGR /
-25.2% max drawdown / 74% win rate (short strikes ~2% OTM, wings ~1%
further out, weekly cycle, realistic options STT + brokerage modeled, 493
cycles). Both walk-forward halves positive (33.3% and 51.4% CAGR) — no
sign flip. A 9-config perturbation sweep (short_otm x wing width) is
positive everywhere except the tightest/riskiest corner. **Quarter-split
shows no decay** — unlike every prior "promising" finding in this project
(Donchian/BTC, momentum rotation), all 4 quarters are positive AND the most
recent quarter (2024-2026) is the *best* one (+85.7% CAGR), not the worst.
This is the first mechanism tested here that doesn't die under the same
scrutiny that killed everything else.

**But it's disqualified anyway, on a different axis: position sizing at
this project's capital tier is structurally impossible to do safely.**
Nifty's current lot size (65 units, raised under SEBI's minimum-contract-
value rules) means even ONE defined-risk spread's max loss (wing width
minus credit received, x lot size) runs roughly ₹6,500-9,500 at typical
2026 Nifty levels — **20-30% of a ₹30,000 account in a single trade**,
10-20x this project's own stated 1-2% risk-per-trade standard (see
README's capital-requirements note). The quarter-split run surfaced this
concretely: Q3 (2021-2024) shows a **-130.4% drawdown** — mathematically
only possible because the probe kept trading a fixed 1 lot regardless of
shrinking capital, with no circuit breaker (exactly the class of bug
`risk.py`'s drawdown breaker exists elsewhere in this repo to catch,
reproduced here because this probe never wired one in). Sizing down to a
genuine 1-2% risk per trade isn't possible with fractional lots — it would
require roughly ₹350,000-475,000 of capital to make ONE lot's max loss a
sane fraction of the account. That's the same wall the Nifty-futures
overnight-carry idea hit earlier in this file, now shown to apply to
options as well: Nifty's contract size itself is incompatible with
"low capital," independent of whether the underlying edge is real.

**Net verdict**: the first genuinely non-decaying, statistically coherent
edge found in this whole project (short volatility / theta harvesting) —
and still not usable at the capital level this project has targeted, for a
structural reason (minimum lot size) rather than the noise/decay/thin-edge
reasons that killed everything else. Two honest paths if pursued further:
validate against real historical option-chain data before trusting the
synthetic-pricing result at all, and separately accept that trading it
properly needs meaningfully more capital (mid-lakhs, not tens of
thousands) — "low capital Nifty options selling" is close to a contradiction
in terms at 2026 lot sizes.

## Eighth: sector/stock diversity sweep — no sector shows a hidden edge either

Direct test of "maybe a specific sector or stock, not the broad blends
already tried, is where one of these mechanisms actually works." Reused
this repo's own tested code (`daily_strategy.py`'s two strategies,
`backtest_daily.py`'s `simulate_daily`/`walk_forward_daily`/
`fetch_daily_yfinance`) rather than reimplementing, across 8 sectors x ~5
stocks each (IT, Banking/Financials, FMCG, Pharma, Auto, Metals,
Energy/PSU, Cement/Infra/Telecom — 39 valid symbols, TATAMOTORS.NS failed
to fetch) x {donchian, rsi2} = 78 walk-forward tests, screening on "both
halves positive, no drawdown halt."

**14/78 (18%) passed the initial screen — at or below what pure chance
alone predicts** for two independent coin-flip halves (~25% expected).
That's the headline finding on its own: this sweep is statistically
indistinguishable from noise before even looking at which 14 passed.

Ran the two strongest-by-magnitude passers through the same
perturbation + quarter-split rigor used everywhere else in this file:

- **ONGC.NS (Donchian)**: decays to negative in Q4 (2024-2026,
  `P&L -547`) — the identical recent-quarter-decay pattern already seen on
  Donchian/BTC and momentum rotation. Not a new finding, a repeat of the
  same one.
- **VEDL.NS (Donchian)**: the closest thing to a survivor — smooth across
  a 6-point `entry_period` perturbation sweep (10 to 90 days, only one
  near-zero dip), and 3 of 4 quarters positive including the most recent
  (`Q4 +373`). But the magnitude is negligible either way — a few hundred
  to a few thousand rupees per multi-year half on ₹100,000 capital, well
  under 1%/year — and it surfaced from a 78-test sweep whose overall hit
  rate was already at chance level. Not distinguishable from a lucky draw.
- VEDL.NS's RSI-2 pass and JINDALSTEL.NS's RSI-2 pass were both weaker
  still under perturbation (sign flips at nearby thresholds, magnitude
  collapsing toward zero between halves) and quarters alternating
  sign with no coherent pattern — noise, not signal.

**Net verdict**: broadening the search across individual stocks and
sectors, rather than the large-cap/index blends tried earlier, changes
nothing. No sector shows a concentration of real edge; the apparent hits
are exactly what a search this wide produces by chance. Combined with the
seven mechanism categories above, this is the point where "try a different
stock/sector/mechanism" stops being a productive search direction on
simple technical/statistical rules over liquid NSE instruments — the
option-selling result remains the one exception, real but blocked by
capital-tier structure rather than by noise.

## Ninth: trying to fix the iron condor's capital problem — halving position size doesn't help, and it's a SEBI-wide wall anyway

Two follow-ups to the capital-tier problem the iron condor hit, both aimed
at making options premium selling actually reachable at lower capital.

**1. Single-sided, trend-filtered put credit spread** (sell a put OTM, buy
a further put as protection — one spread instead of the iron condor's two,
only entered when spot is above its 200-day SMA). Same synthetic
Black-Scholes/India-VIX pricing as the iron condor. Genuinely better risk
profile than the plain iron condor: no quarter-split decay (Q4 2024-2026 is
the *strongest* quarter, smallest drawdown, not the weakest), both
walk-forward halves positive and improving. **But safely sized (~2%
risk/trade, ~₹300,000 capital), it nets only +1.3%/year** — a 9-config
perturbation sweep is positive everywhere but every cell is between +0.2%
and +2.2%/year. Same lesson as RSI-2 and the full iron condor: halving
position size does not fix a thin edge, because return and risk dilute
together at safe sizing — it just moves the "unsafe-but-good-looking vs.
safe-but-negligible" tradeoff to a smaller absolute capital number.

**2. Is a different index/instrument the fix? No — this is a SEBI-wide
regulatory floor, not a Nifty quirk.** Confirmed via research: SEBI's
November 2024 rule (effective April 2025) mandates **every** index
derivatives contract — Nifty, Bank Nifty, FinNifty, Midcap Nifty, Nifty
Next 50, any index, any future index — be sized so its contract value sits
in a ₹15-20 lakh band at introduction. Bank Nifty's lot size dropped to 30
specifically *to hit this same band* at its own price level, not to be
smaller — so switching indices doesn't change the max-loss-per-lot
economics at all, only the lot-size number cosmetically. Individual STOCK
options have a lower floor (~₹7.5 lakh minimum contract value, up from
₹2-4 lakh pre-2025) — roughly half the capital of an index spread — but
this project's own sector/stock sweep above already found no stock-specific
edge bigger than the ones already measured, so a lower capital floor there
doesn't change the underlying magnitude problem, just its absolute scale.

**Net verdict**: the capital-tier wall around options premium selling in
India isn't something a cleverer construction (single-sided vs.
symmetric) or a different underlying escapes — it's a regulatory
minimum-contract-value floor applied market-wide since April 2025, and even
where it's lower (individual stocks), the same "edge dilutes to
sub-benchmark at safe sizing" finding already established elsewhere in this
file still applies. This closes out the options-premium-selling line
credibly rather than leaving it as "just needs more capital" — more capital
alone was never going to fix a thin edge, and less capital was never
achievable given the regulatory floor.

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

## Tenth: intraday gap-fill mean reversion — same failure pattern as everything else

Sourced from repeated (loosely-quantified) trading-forum/blog claims about
NSE gap behavior — a genuinely different mechanism from anything above:
bets on reversion of the OPENING GAP itself within a single session, not a
multi-bar trend/reversion/spread signal. Rule: `gap_pct = (open -
prior_close) / prior_close`; if it exceeds a threshold, trade back toward
`prior_close` (short a gap-up, long a gap-down), stop a fixed % beyond the
open, exit at target/stop/close whichever the day's OHLC path hits first
(same conservative adverse-first approximation used elsewhere in this
file). Implemented as `probe_gap_fill.py` — a standalone script, not ported
into `daily_strategy.py`'s architecture, because the entry decision and
price both depend on the day's OPEN relative to yesterday's close, which
the existing `push(bar)`/`check_entry(close)` interface doesn't expose
(same reasoning as the momentum-rotation and pairs-trading probes above).

**Result: fails on all four checks that have caught every prior false
positive.** On `^NSEI` (10y, ~0.05%/trade all-in cost approximating NSE
intraday equity STT+charges+brokerage): net P&L is negative below a
~0.75% gap threshold (390 trades at 0.5% threshold, gross +5,603 but net
-14,061 — the now-familiar high-frequency-cost-drag pattern) and only
marginally positive above it (best case +1.97%/yr at threshold=1.0%,
stop_extra=0.3%, which IS smooth/monotonic as stop_extra widens — the one
axis that behaved like a real parameter). But: **walk-forward on that exact
config is sign-flipped** (in-sample +2.49%/yr, out-of-sample -0.46%/yr,
INCONSISTENT), **quarter-split is mixed** (Q1/Q2/Q4 positive, Q3 -2.18%/yr),
and **the "edge" doesn't generalize across instruments** — the same
threshold sweep is net-negative almost everywhere on `^NSEBANK` and
`RELIANCE.NS` (best cases there: -0.21%/yr and +0.20%/yr respectively,
i.e. noise-floor). One index, one narrow threshold band, inconsistent
across both time and instrument — textbook curve-fit, not a real pattern.

**Net verdict**: tenth mechanism, tenth failure, and the specific way it
fails (looks OK in aggregate, falls apart on every split) is now such a
recurring signature in this project that it's worth naming: **any
candidate that only "works" within a narrow parameter band, on one
instrument, and doesn't reproduce across both walk-forward halves AND all
four quarters should be treated as noise by default**, not investigated
further parameter-by-parameter looking for the config that survives.

## Eleventh: profit-booking overlay on RSI-2 — actively hurts, doesn't help

Directly tests the "booking profit" question against the one strategy in
this project with a real, robust, but too-thin edge (Connors RSI-2, see
above) — the exact case where a better exit could plausibly move the
magnitude verdict. Wired `strategy.py`'s existing `TrailingStopManager`
back in for `daily_strategy.py` strategies (previously always disabled via
`breakeven_trigger=trail_trigger=float("inf")`, see the bug story above)
but scaled to the position's OWN entry-time ATR instead of reusing the
forex-pip defaults that caused that bug: `ConnorsRSI2Strategy.current_atr()`
(new public wrapper around the existing private `_current_atr()`) feeds
`backtest_daily.py --breakeven-atr-mult` / `--trail-atr-mult`, which
`simulate_daily()` only applies when both are set AND the strategy exposes
`current_atr()` (Donchian doesn't, so it's unaffected either way — default
`None`/`None` reproduces the old inf/inf behavior exactly, checked by a
regression test).

**Result: makes every single instrument tested worse, not better.**
Compared baseline (no profit-booking) against `--breakeven-atr-mult 1.0
--trail-atr-mult 2.0` across all 7 instruments that traded at all (10y,
₹20/trade commission): `^NSEI` -1,324 -> -2,210, `RELIANCE.NS` +774 ->
-517, `INFY.NS` +638 -> -263, `HDFCBANK.NS` +604 -> -242, `ITC.NS` -442 ->
-1,104, `CL=F` -22 -> -610, `TCS.NS` (the best baseline performer) +1,104
-> +440 — positive in only one case and still cut by more than half.
Widening the triggers to 2+ ATR never fires at all (RSI-2's mean-reversion
moves apparently never run that far before the existing SMA-cross exit
catches them), reproducing the baseline exactly; the only range where the
overlay does anything (roughly 0.5-1.5 ATR) is exactly where it hurts.

**Why**: RSI-2's exit rule (close back through the 5-day SMA, or the trend
filter flipping) already IS a profit-taking rule tuned to this specific
mean-reversion mechanism — it rides a reversion move to what is
functionally close to its natural target. Locking in profit earlier via a
generic ATR trail doesn't protect gains against a mechanism the strategy
was already benefiting from; it just truncates the same moves the SMA-exit
was already capturing efficiently, converting bigger wins into smaller
wins or (when the tightened stop sits exactly at breakeven) commission-only
losing scratches.

**Net verdict**: "add a profit-booking overlay" is not a free lever — it
has to match the entry mechanism's own natural holding period/target, not
be bolted on generically. For a mean-reversion strategy whose own exit rule
already functions as a profit target, an independent trailing-profit lock
is actively counterproductive. The mechanism (ATR-scaled `TrailingStopManager`
reuse) is now available to any future daily strategy via `--breakeven-atr-mult`/
`--trail-atr-mult` where it might genuinely apply (e.g. a trend-following
entry with no natural profit-taking exit of its own) — just not this one.

## Twelfth: 3-bar compression breakout, sourced from ForexFactory — the best hit rate yet, still thin at safe sizing

Dedicated research pass through ForexFactory specifically (the same forum
`strategy.py`'s own Highest-Open/Lowest-Open source came from), per the
user's request to look there again for newer material rather than just
sweeping more instruments on an existing mechanism. Pulled a concrete,
rule-specific pattern from the "Daily chart trading - simple entry and exit
criteria" thread (forexfactory.com/thread/1126565) and the related "Daily
Chart 3-Candle" / "3 Consecutive Candles Method" threads (759887, 758687):
two consecutive daily bars closing near the same level ("compression"),
followed by a third bar closing decisively beyond both ("breakout") signals
continuation — stop a fixed pip buffer past the compression zone's
structural low/high, take-profit a fixed pip target (source: ~100-200 pips
against ~50-65 pips risk, roughly 2-3R). Genuinely different mechanism from
every strategy already in this file: not a channel breakout (Donchian), not
mean-reversion (RSI-2), and the first one here with a FIXED profit target
instead of a moving exit condition.

Implemented as `ThreeBarBreakoutStrategy` in `daily_strategy.py`
(`--strategy threebar` in `backtest_daily.py`), with the same pip-to-ATR
generalization this project has had to apply everywhere else a forex
source gave raw pip numbers (see the Donchian section above and the
TrailingStopManager bug): `compression_atr_mult` (bar1/bar2 must be within
N ATRs of each other, default 0.5), `stop_buffer_atr_mult` (stop N ATRs
past bar2's structural level, default 0.5), `target_r_multiple` (fixed
take-profit at N x risk, default 2.5). Added `max_hold_days` (default 20)
as a deliberate addition beyond the source thread — a fixed-target trade
with no time stop can sit open indefinitely on a sideways drift, and every
other strategy here already has some bounded holding mechanism.

**Screening result: 6/12 instruments passed (both walk-forward halves
positive, no drawdown-halt)** — `INFY.NS`, `TCS.NS`, `HDFCBANK.NS`,
`SBIN.NS`, `CL=F`, `GC=F`. A 50% hit rate, well above the sector sweep's
chance-level 18% and better than anything except RSI-2's 57%. Failed:
`^NSEI`, `AXISBANK.NS`, `ITC.NS` (all consistently negative, `ITC.NS` also
drawdown-halted both halves), `^NSEBANK`, `RELIANCE.NS`, `WIPRO.NS` (sign
flips between halves).

**Quarter-split (four ~2.5y chunks over 10y) on the five passers beyond
INFY.NS is genuinely stable** — no severe decay, only mild single-quarter
dips (`SBIN.NS` Q3 -867, `CL=F` Q1 -142, `GC=F` Q2 -2289, all small relative
to the other quarters), and none of the "most recent quarter is the worst
one" pattern that killed Donchian/BTC and momentum rotation. `INFY.NS`
itself is the one exception worth flagging: 10,058 of its ~13,753 10-year
total P&L is concentrated in the single 2019-2021 quarter (COVID crash and
recovery) — the same regime-concentration signature already distrusted
elsewhere in this file, so treat `INFY.NS`'s result as weaker evidence than
the other four.

**Parameter perturbation on `INFY.NS`** across `compression_atr_mult`
(0.25-1.0), `target_r_multiple` (1.5-4.0), and `stop_buffer_atr_mult`
(0.25-1.0) is mostly smooth and all-positive, with one soft cliff at the
extreme low end (`target_r_multiple=1.5`'s out-of-sample half goes
negative) — better robustness than most candidates tested in this project,
on par with RSI-2's.

**But magnitude is still the limiting factor, same lesson as RSI-2 and
every "found it" before falling to it**: at the standard 0.5% risk-per-
trade, the five non-INFY-anomalous passers return 0.4-2.2%/year net of a
₹20/round-trip commission on ₹100,000 capital — thin, comparable to a
fixed deposit. **Unlike RSI-2, more aggressive (but still conservative)
sizing genuinely helps here rather than just scaling drawdown alongside
return**: at 1% risk-per-trade (still well inside a sane range, not the
reckless 10% RSI-2 needed to test), `SBIN.NS` reaches 4.56%/year at 1.2%
max drawdown, `INFY.NS` 3.67%/year at 1.2%, `HDFCBANK.NS` 3.81%/year at
1.0%, `CL=F` 2.87%/year at 4.4%, `GC=F` 1.87%/year at 2.4% — real
year-over-year growth at very low drawdown across five independent
instruments, not a single cherry-picked one. `TCS.NS` stays weak (0.36%/yr)
even at 1% risk. Pushing further to 2-3% risk-per-trade breaks this: both
`SBIN.NS` and `INFY.NS` hit the drawdown-halt breaker (25-27% drawdown) at
that point, so 1% appears to be close to the safe ceiling for this specific
strategy, not a floor to push past.

**Net verdict**: the best-documented hit rate and quarter-split stability of
any strategy family in this project besides options-selling, backed by a
real, specific, cited source (unlike some of the vaguer candidates
ForexFactory search turned up, which were skipped for lacking testable
rules) — and the first strategy where modest sizing (not reckless
leverage) turns a thin edge into a genuinely non-negligible 2-4.6%/year
across multiple instruments at low single-digit drawdown. Still requires
picking the right instrument (50% hit rate, not universal) and treating
`INFY.NS` specifically with the regime-concentration caveat. Worth
revisiting with more instruments and a real (not flat ₹20) NSE-equity cost
model before calling this "found," but it's the most promising single
result since the options-selling line closed out on capital-tier grounds.

## Thirteenth: Internal Bar Strength (IBS) mean reversion — one gold survivor, at-chance hit rate

Widened the research net beyond ForexFactory per the user's request to keep
searching for the 3-bar breakout's specific profile (consistent, and sizing
UP genuinely helps rather than just diluting): Reddit-adjacent quant sources
this time — Alvarez Quant Trading's "Internal Bar Strength for Mean
Reversion", Jonathan Kinlay's "The Internal Bar Strength Indicator", and
QuantifiedStrategies.com's IBS writeups, all citing a documented decades-long
edge on broad equity indices. Genuinely different mechanism from everything
above: IBS = (close - low) / (high - low) is a SAME-DAY positional signal —
where today's close fell within TODAY's own high-low range — not a multi-day
momentum oscillator (RSI-2) or a channel/pattern breakout (Donchian,
ThreeBarBreakout). Published rule: long when IBS < ~0.2 ("closed near the
day's low"), exit when IBS closes back above ~0.5.

Implemented as `probe_ibs.py`, a standalone script rather than a
`daily_strategy.py` addition — `check_entry(close)`'s interface only exposes
TODAY's close, not today's own high/low, which IBS specifically needs at the
entry decision (same shape-mismatch reasoning as the momentum-rotation,
pairs-trading, and gap-fill probes). Symmetric short side (IBS > 0.8) added
for consistency with this project's other strategies but flagged explicitly
as this project's own extension, not itself literature-backed — the
published edge is specifically the long side. ATR-based stop (no natural
structural stop in the published rule, same situation RSI-2 was in) and a
10-day time-stop (this project's standard bounded-hold convention).

**Screening result: 1/8 instruments passed (both walk-forward halves
positive)** — only `GC=F` (gold). Failed: `^NSEI`, `^NSEBANK`,
`RELIANCE.NS`, `HDFCBANK.NS` (all consistently negative), `TCS.NS`,
`INFY.NS` (sign flips between halves). A 12.5% hit rate — at or below the
sector sweep's already-distrusted 18% "chance level" finding, well short of
the 3-bar breakout's 50%. On this project's own screening standard, this
does not clear the bar of a broad, real edge.

**The one survivor is not a hollow fluke, for what that's worth**: `GC=F`'s
quarter-split is 3/4 positive with the most recent quarter (Q4, +1.18%/yr)
the *best* one, not the worst — no regime-decay red flag. A 5-point
`ibs_entry_long` perturbation (0.10 to 0.30) is smooth and monotonic
(0.33% to 0.72%/yr, no cliffs). And it does show the specific property being
searched for: **sizing from 0.5% to 1% risk-per-trade more than triples the
return** (0.66%/yr -> 2.21%/yr) while drawdown only grows 2.5% -> 3.9% —
sizing helps here too, not just the 3-bar breakout.

**Net verdict**: a real research trail with a credible, well-cited mechanism
that produced exactly one instrument clearing the bar, at a hit rate
statistically indistinguishable from the noise floor already established by
the sector sweep. The single survivor's internals (perturbation, quarter-
split, sizing-helps) look like the real thing, but "one instrument out of
eight, on a mechanism whose published edge is on indices and this is a
commodity" is thin ground to stand on alone — this is closer to "flag and
retest with more instruments/parameters later" than "found," unlike the
3-bar breakout's broader six-instrument confirmation. Not ported into
`daily_strategy.py`'s tested architecture for that reason — it stays a probe
script, same treatment as gap-fill (dead) and momentum-rotation/pairs-trading
(decayed), until either more instruments confirm it or it's dropped.

**Also researched, not tested**: Toby Crabel's NR7 (single-day range-
contraction breakout, close cousin of `ThreeBarBreakoutStrategy`'s 2-day
compression but 7-day and single-bar) came up in the same search pass. Not
implemented — multiple independent sources (QuantifiedStrategies.com among
them) already report it failing to survive realistic transaction costs, the
exact failure mode this project has already confirmed repeatedly on similar
high-frequency pattern setups (SI=F, the overnight-drift trade). Skipped to
avoid re-spending backtest effort re-confirming a well-documented negative
result rather than testing something genuinely untried.

## Fourteenth: validating the iron condor against REAL option-chain data — edge direction confirmed, magnitude drastically smaller than Black-Scholes implied

The "Seventh" entry above flagged its biggest weakness explicitly: the
iron condor's +20.5% CAGR came from Black-Scholes premiums synthesized with
a flat India-VIX IV input, not real market prices, and "validate against
real historical option-chain data" was listed as the open next step. This
entry does that, using genuinely free data sources (no paid Kite Connect
"Historical" tier, no subscription):

- **`jugaad-data`** (MIT-licensed, PyPI) for NSE's old-format F&O bhavcopy
  (`bhavcopy_fo_raw`), which covers roughly 2015 through mid-2024 reliably
  (verified working back to 2015, ~0.5s per fetch, no rate-limiting hit in
  ~150 sequential calls a few hundred ms apart).
- NSE's own new-format F&O UDIFF bhavcopy, fetched directly once the URL
  pattern was found by inspecting the CM-segment UDIFF path jugaad-data
  already knows about and probing the equivalent `/content/fo/
  BhavCopy_NSE_FO_..._F_0000.csv.zip` path on `nsearchives.nseindia.com` —
  the installed jugaad-data version's own `bhavcopy_udiff_raw` only covers
  the equity/CM segment, not F&O, so this had to be done as a plain
  `requests` call rather than through the library. Verified working from
  mid-2024 through September 2026 (today).

Together these give genuinely real historical NIFTY option settlement
prices (OHLC + settlement per strike/expiry) end to end, 2015-2026, for
free. **This resolves this project's biggest single open data gap.**

**Method** (`probe_iron_condor_real_data.py`, standalone, not ported into
the tested architecture): same parameters as the synthetic run (short
strikes ~2% OTM, wings ~1% further out, weekly cycle, current 65-unit lot
size, ~₹100/cycle flat cost estimate for 4-leg brokerage+STT). Per cycle:
fetch the real bhavcopy on entry day, find the real CLOSE price of the
exact 4 strikes nearest the target OTM levels (real net credit, not a
Black-Scholes estimate — and unlike the synthetic version, this
automatically reflects real volatility skew across strikes, since it's
reading actual traded/settled prices per strike rather than applying one
flat IV to all four legs), then settle at expiry using `^NSEI`'s real
close on the expiry date.

**Result, 140 real weekly cycles, 2024-01-01 to 2026-08-31** (chosen to
directly re-test the specific "Q4 2024-2026 is the best quarter, +85.7%
CAGR" claim from the synthetic run, since that's the most recent and most
decision-relevant window):

- Total net P&L: **₹25,907** over ~2.67 years, 77.1% win rate (108/140) —
  close to the synthetic run's 74% win rate, so the qualitative hit-rate
  claim holds up.
- **Walk-forward both halves positive** (₹2,181 first half, ₹23,725 second
  half, 70 cycles each) — no sign flip, corroborating the synthetic run's
  "no decay" finding directionally.
- Quarter-split is NOT uniformly smooth on real prices, though — three
  quarters are meaningfully negative (2024 Q2 -₹3,604, 2024 Q4 -₹9,188)
  even though the overall trend across the period is toward strength (2025
  Q2 alone was +₹16,644). Real quarter-to-quarter variance is higher than
  the synthetic run's clean "all 4 quarters positive" picture.
- **The magnitude gap is the real finding**: annualized, ₹25,907 over 2.67
  years is **~₹9,700/year**. At the ₹350,000-475,000 capital this
  strategy structurally needs to size one lot safely (per the Seventh
  entry's own math), that's **only ~2.0-2.8%/year** — a small fraction of
  the +20.5% CAGR (and nowhere near the +85.7% CAGR claimed for this exact
  calendar window) the Black-Scholes synthetic version reported. Real
  market option pricing collects meaningfully less net credit than a
  flat-IV model assumes, once actual skew/liquidity/pricing efficiency
  across strikes is accounted for by construction (this backtest reads
  real traded closes, not a model).
- Caveat in the other direction: this still uses settlement/last-close
  prices as fills, not bid/ask-executed prices, and models no slippage —
  real achieved returns are likely *below* this ~2-2.8%/year, not above
  it. No perturbation sweep was re-run on real data (time-boxed); the
  walk-forward + quarter-split were prioritized as the higher-value checks
  given the primary question was "does real data corroborate the
  synthetic edge's existence," not full robustness re-certification.

**Net verdict**: the direction of the edge is real — it survives contact
with actual market prices, unlike almost everything else in this project.
But the magnitude was substantially inflated by the Black-Scholes/flat-IV
synthesis, the same lesson this project has learned repeatedly about not
trusting a backtest number until it's checked against something more
real (transaction costs, walk-forward, now real pricing data). Combined
with the Seventh/Ninth entries' capital-tier finding, this closes the
loop rather than reopening it: even fully funded at ₹350-475k, real
returns here (~2-3%/year) are below what a risk-free instrument pays,
which is a second, independent reason (on top of the capital-access
problem) this strategy isn't worth pursuing further at this project's
scale. The open methodological win, independent of this specific
strategy's fate, is that real NSE F&O historical data is now known to be
freely obtainable — any future options-pricing-dependent idea in this
project should use `probe_iron_condor_real_data.py`'s fetch/parse
functions as a starting point instead of falling back to Black-Scholes
synthesis.
