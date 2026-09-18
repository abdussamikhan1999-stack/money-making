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

## Fifteenth: TTM Squeeze (Bollinger-Keltner squeeze breakout) — hit rate at chance level, one survivor's robustness doesn't hold up

Per the user's request to pull from actual OPEN-SOURCE STRATEGY CODE this
time (not forum rule descriptions) — searched GitHub, TradingView's public
script library, and freqtrade/backtrader-adjacent implementations for a
genuinely different mechanism. Picked John Carter's TTM Squeeze in the exact
formulation of LazyBear's "Squeeze Momentum Indicator [LazyBear]" — the
canonical open-source reference implementation, mirrored/forked across
dozens of GitHub repos (e.g. `fmzquant/strategies`'
Squeeze-Momentum-Indicator.md, `indie-script.github.io`'s writeup) and one
of the most widely-copied technical indicators that exists. Genuinely
different mechanism from everything else in this file: not a price-channel
breakout (Donchian), a level-based mean reversion (RSI-2), or a fixed-
pattern continuation (3-bar breakout) — it's a VOLATILITY-STATE timing
signal. Core idea: Bollinger Bands contracting to sit entirely inside
Keltner Channels means the market is coiled ("squeeze on"); BB expanding
back outside KC ("squeeze fires") tends to be followed by a directional
move, with a linear-regression-based momentum value giving the direction.

Implemented as `SqueezeMomentumStrategy` in `daily_strategy.py`
(`--strategy squeeze` in `backtest_daily.py`), reproducing LazyBear's script
exactly: shared BB/KC `length` (default 20, matching the original), `bb_mult`
(2.0)/`kc_mult` (1.5) also the original's defaults. Required three new pure
indicators in `indicators.py` (`stdev`, `highest`/`lowest`, `linreg` — a
closed-form OLS fit, no numpy, consistent with this project's plain-float
style) since Bollinger Bands and the momentum histogram weren't needed by
anything tested here before. No natural structural stop exists (same
problem RSI-2 had), so the initial stop is `stop_atr_multiple` x ATR,
reusing the Keltner Channel's own ATR rather than a second computation.
Exit: momentum flipping sign against the position, or `max_hold_days` as a
time-stop — this file's now-standard bounded-holding convention. Unlike
RSI-2 (which needs today's own close to detect today's dip), sqzOn/val here
are computed entirely from the window already pushed (today's close used
only as the traded price, never fed into the rolling window) — a squeeze
calculation is a slow 20-bar-wide statistic, so being one bar "behind"
doesn't change its character the way it would for a 2-period RSI. 20 new
unit tests (8 for the new indicators, 7 for the strategy — pure logic,
exact-value assertions via hand-verified compression/breakout fixtures);
full suite 87/87 green (`pytest -v`).

**Screening result: 3/12 instruments passed (both walk-forward halves
positive, no drawdown-halt)** — `TCS.NS`, `INFY.NS`, `GC=F`. A 25% hit
rate — right at the "pure chance" level for two independent coin-flip
halves, the same disqualifying signature already established in this
file's Eighth entry (the sector sweep's 18% hit rate, "statistically
indistinguishable from noise before even looking at which passed"). Failed:
`^NSEI`, `^NSEBANK`, `ITC.NS`, `SBIN.NS` (consistently negative both
halves); `RELIANCE.NS`, `HDFCBANK.NS`, `AXISBANK.NS`, `WIPRO.NS`, `CL=F`
(sign flips between halves).

**Quarter-split on the three passers is inconsistent, not clean like the
3-bar breakout's five non-anomalous survivors**: `TCS.NS` has 2 of 4
quarters negative (-1898, -473) despite passing the 2-way walk-forward
screen — the same "hollow consistency" pattern this file's Donchian section
warns about, where a coarser split misses decay a finer one catches.
`GC=F` has one negative quarter (-674) but is otherwise stable. Only
`INFY.NS` is genuinely clean: all four quarters positive (+1424, +3148,
+2055, +2130) with no single-quarter concentration this time (unlike the
3-bar breakout's `INFY.NS`, whose edge was 73% concentrated in one COVID
quarter — this one is much more evenly spread).

**Parameter perturbation on `INFY.NS`'s `length`** is NOT smooth in the
wide view — length=10 is negative and drawdown-halted (-7,083), 15/20/25
are all positive (+15,837/+20,614/+10,144, peaking suspiciously close to
the exact default), length=30 goes negative (-712), length=40 is barely
positive on only 4 trades (unreliable sample). In the narrower 15-25
neighborhood it's smoother, and cross-checking `TCS.NS`/`GC=F` across the
same range shows both stay positive throughout (+17,208/+9,209/+15,749 and
+9,654/+15,665/+7,628) — so it isn't a knife-edge single-point fit, but the
10/30/40 extremes behaving erratically is still a weaker robustness profile
than the 3-bar breakout's "mostly smooth, one soft cliff" result.
`kc_mult` perturbation on `INFY.NS` (1.0 to 2.0) is more reassuring —
monotonically increasing from -4,289 at 1.0 to +33,299 at 1.75, dipping
slightly at 2.0 — no violent cliff there.

**Sizing does help, same property the 3-bar breakout has and RSI-2
doesn't**: `INFY.NS` at 0.5% risk-per-trade returns ~0.94%/year (0.5% max
drawdown); at 1% risk it's ~2.06%/year (1.0% max drawdown) — genuine
doubling, not dilution. Pushing to 2% breaks it (drawdown-halted, -0.05%/yr)
— the same "1% is close to the safe ceiling, not a floor to push past"
pattern already seen with the 3-bar breakout.

**Net verdict**: this strategy does NOT beat, or even clearly match, the
3-bar breakout. It shares one attractive property (sizing helps) but fails
on the more fundamental screen — a 25% hit rate is statistically
indistinguishable from chance, the same standard that already disqualified
VEDL.NS's sector-sweep "survival" in the Eighth entry. `INFY.NS`'s
individual result looks clean on quarter-split and reasonable on
perturbation, but surfacing from a chance-level sweep means it isn't
distinguishable from a lucky draw either — the same conclusion this file
already reached once before and should keep applying consistently rather
than re-litigating every time a single instrument looks good in isolation.
Left as `--strategy squeeze` in the tested architecture (unlike some
probe-script-only explorations) since the indicators and strategy code
themselves are reusable and correctly tested, even though this particular
screening result doesn't clear the bar to recommend trading it.

## Sixteenth: Chaikin Money Flow + On-Balance Volume confirmation — one clean survivor (gold), too thin to size up

Sourced from real open-source strategy CODE again (per the same standard
the Squeeze entry set): `XBT3K/VOLUME-ALGO-EURUSD` on GitHub, a runnable
backtrader strategy (`VolumeOBVCMF`) that buys when both Chaikin Money Flow
and On-Balance Volume are positive and closes when both turn negative.
Genuinely different mechanism from all fifteen prior entries: this is the
**first strategy in this project to use volume at all** — everything before
this (Donchian, RSI-2, 3-bar breakout, IBS, Squeeze) is price-only.

Implemented as `VolumeConfirmationStrategy` in `daily_strategy.py`
(`--strategy volume` in `backtest_daily.py`), with two adaptations from the
source, both interpretation decisions:
- **OBV windowed, not cumulative-since-inception.** The source's raw OBV
  crossing a fixed "0" is only meaningful relative to wherever backtrader's
  running sum happened to start at the beginning of that data feed — not
  comparable across the different time windows this project's walk-forward
  and quarter-split checks require. `indicators.on_balance_volume()` sums
  signed volume over a rolling `period` window instead (same windowed
  philosophy as `average_true_range`/`rsi` already have in this file),
  making the 0-crossing a stable "more up-volume than down-volume in the
  last N days" signal.
- **Symmetric short side added** (the source is long-only) — this
  project's own extension for consistency with every other strategy here,
  same caveat already applied to `probe_ibs.py`'s short side.

New indicators `chaikin_money_flow()`/`on_balance_volume()` added to
`indicators.py` (7 new unit tests), plus 8 new strategy tests — full suite
100/100 green.

**A real applicability limit surfaced immediately, not a bug**: yfinance
reports zero volume for index symbols (`^NSEI`, `^NSEBANK` — an index
itself has no traded volume), so `chaikin_money_flow()` correctly returns
`None` and the strategy simply never trades them. Screened stocks/futures
only this time (10 Kite-tradable instruments: `RELIANCE.NS`, `TCS.NS`,
`INFY.NS`, `HDFCBANK.NS`, `ITC.NS`, `SBIN.NS`, `AXISBANK.NS`, `WIPRO.NS`,
`GC=F`, `CL=F`).

**Screening result: 3/10 passed (both halves positive, no drawdown-halt)**
— `INFY.NS`, `WIPRO.NS`, `GC=F`. A 30% hit rate, marginally above the
"pure chance" band this project has repeatedly used as a disqualifying
signal (sector sweep 18%, Squeeze 25%) but not comfortably clear of it
either. `TCS.NS` and `SBIN.NS` were sign-CONSISTENT across both halves but
both **negative** — a consistent loser, not a passer; the walk-forward
check alone doesn't distinguish the two, which is exactly why this file's
screening standard has always been "both halves positive," not just
"matching sign."

**Quarter-split separates the three passers cleanly** (four ~2.5y chunks
over 10y): `INFY.NS` and `WIPRO.NS` both show the same recent-quarter-decay
signature already disqualifying elsewhere in this file — Q4 (2024-2026,
the most recent and most relevant window) is **negative** for both
(-262, -2,845) despite 3 of their 4 quarters being positive. Only `GC=F`
(gold) is genuinely clean: 3 of 4 quarters positive, and Q4 is not just
positive but the **strongest** quarter (+6,056) — no decay red flag, same
"most-recent-quarter-is-best" pattern the options-selling line saw before
its capital problem, not the "historic run now flat" pattern that killed
Donchian/BTC and momentum rotation.

**`GC=F`'s perturbation sweep is smooth and all-positive** — `cmf_period`
10 through 30 and `obv_period` 10 through 30 both stay positive on both
walk-forward halves at every tested value, no cliffs, no sign flips. This
is real robustness, on par with RSI-2 and the 3-bar breakout, and clearly
better than Squeeze's erratic `length` sweep.

**But sizing doesn't help here — GC=F fails the same way RSI-2 did, not
the way 3-bar breakout/Squeeze/IBS did**: raising `--risk-per-trade-pct`
from the default 0.5% to 1% or 2% doesn't scale the return up, it
DRAWDOWN-HALTS the run almost immediately (10.2%/10.9% drawdown, trade
count collapsing from 192 to 38 to 18) — return and risk are diluting
together, not compounding. Stuck at the default-sizing magnitude:
**+12,969.80 net P&L over the full 10-year period on ₹100,000 capital is
~1.30%/year** — thin, comparable to RSI-2's and the options single-sided
credit spread's verdicts, well short of a usable edge.

**Net verdict**: sixteenth mechanism, and the first one to genuinely
introduce a new data dimension (volume) rather than a new way of reading
price alone — and it still lands in the now-familiar "real but too thin"
bucket rather than "found it." One instrument (gold) clears every rigor
check in this project (walk-forward, quarter-split, perturbation) as
cleanly as RSI-2 or the 3-bar breakout did, but the 30% hit rate across the
other nine instruments is close enough to chance that a single clean
survivor isn't strong independent confirmation on its own, and — unlike
the two strategies here that turned "thin" into "usable" via modest
sizing (3-bar breakout, Squeeze) — this one's return and drawdown scale
together the way RSI-2's did, so there's no size-based lever to pull.
Left as `--strategy volume` in the tested architecture since the code
(indicators and strategy) is reusable and correctly tested, same treatment
as Squeeze, even though it doesn't clear the bar to recommend trading it.

## Seventeenth: SuperTrend (ATR ratchet-band trend-following) — one instrument clears every check, but it's a single survivor from a chance-level sweep

Sourced from real open-source strategy CODE again (same standard as
Squeeze/volume): `Nikhil-Adithyan/Algorithmic-Trading-with-SuperTrend-
Indicator-in-Python` on GitHub, one of the most widely-forked/cited
SuperTrend implementations, referenced across many algotrading writeups.
Genuinely different construction from `DonchianBreakoutStrategy` (this
project's other trend-follower): Donchian's channel is a FIXED N-day
high/low; SuperTrend's band is a sticky RATCHET that only ever moves in
the trend's favor until price actually crosses it — a smoother trailing
stop rather than a hard rolling channel.

Implemented as `probe_supertrend.py`, standalone rather than ported into
`daily_strategy.py`'s architecture: the shared `check_entry(close)`
interface only receives today's close, but SuperTrend's own band for day i
is a function of day i's own high/low (that's how the indicator is
actually defined and traded) — the same shape-mismatch reasoning already
applied to IBS, gap-fill, momentum rotation, and pairs trading. Two
adaptations from the source, both interpretation decisions:
- **Plain/unsmoothed ATR** instead of the source's EWMA-smoothed one
  (`tr.ewm(lookback).mean()`) — reuses the same simple rolling-average ATR
  every other ATR-based strategy in this project already uses, rather than
  adding a second smoothing method. The mechanism is the ratchet/flip band
  structure, not the exact ATR smoothing.
- **Bootstrap matches the reference's own actual behavior** (defaults to
  downtrend on the first valid bar) rather than a "which side is price
  already on" heuristic — that heuristic turned out to be a near-always-
  true tautology on the very first bar and silently forced every series to
  bootstrap "uptrend" regardless of real direction. Caught by a synthetic
  all-down-days sanity check (not a pytest file — this project's probe
  scripts don't get one, per existing convention) before trusting any real
  backtest; worth remembering for any future incremental-state indicator
  ported from a vectorized pandas reference.

Exit is the trend flip itself (no separate exit rule needed, unlike RSI-2/
Squeeze/IBS/volume) plus a `max_hold_days` safety cap; the entry's
position-sizing stop is the active band value at entry time, the same
structural-stop role Donchian's own channel plays.

**Screening result: 4/12 passed walk-forward (both halves positive, no
drawdown-halt)** — `HDFCBANK.NS`, `AXISBANK.NS`, `WIPRO.NS`, `CL=F`. A 33%
hit rate, in the same chance-level band this project has repeatedly
treated as inconclusive on its own (sector sweep 18%, Squeeze 25%, volume
30%).

**Quarter-split and perturbation eliminate three of the four**:
- `HDFCBANK.NS`: 2 of 4 quarters negative, and every quarter's magnitude is
  negligible either way (-0.08% to 0.54%/yr) — weak on both counts.
- `WIPRO.NS`: the now-familiar recent-quarter-decay signature — Q4
  (2024-2026) is **negative** (-0.96%/yr) despite 2 of the 3 prior quarters
  being strongly positive.
- `AXISBANK.NS`: quarter-split actually looks fine (3/4 positive, including
  the most recent), but **`st_period` perturbation fails hard** — only the
  exact default (10) has both walk-forward halves positive; 7, 14, and 20
  all either lose outright or sign-flip between halves. This is the same
  "peaks suspiciously close to the exact default" single-point-fit
  signature this project has flagged as disqualifying before (Squeeze's
  `length` sweep) — not real robustness.
- `CL=F` (oil) is the one clean survivor: **quarter-split is all-positive**
  (+0.30%, +0.81%, +0.67%, +0.97%/yr, Q4 the strongest — no decay), and
  **perturbation is smooth on both parameters** (`st_period` 7/10/14 all
  positive both halves, only the wide 20 goes negative on both — a soft
  edge, not a cliff; `multiplier` 2.5 through 4.0 all positive both halves,
  only the tight 2.0 fails).

**Sizing genuinely helps on `CL=F`, the same property the 3-bar breakout
and Squeeze have and RSI-2/volume-CMF-OBV don't**: 0.5% risk-per-trade
gives 0.89%/year (1.9% max drawdown); 1% gives 1.87%/year (3.8% DD); 2%
gives **3.78%/year at 7.4% DD** — a clean roughly-linear scaling, not
dilution. 3% breaks it (drawdown-halted, negative) — 2% is the safe
ceiling here, not a floor to push past, the same shape as every other
"sizing helps" candidate in this project.

**Net verdict**: seventeenth mechanism, and by the numbers on `CL=F`
specifically — quarter-split, perturbation, AND sizing all behaving the
way a genuine, tradable edge should — this is comparable in quality to the
best individual results in this project (RSI-2, 3-bar breakout). But the
context matters: it is **one surviving instrument out of twelve, from an
initial screen already at the chance-level hit rate** (33%), with the
other three "passers" eliminated by the exact checks (quarter-split,
perturbation) that are supposed to separate real edges from noise. The
same conclusion this project reached for IBS's lone gold survivor in the
Thirteenth entry applies again here: a single instrument's internals
looking clean is not strong independent confirmation when it emerged from
a sweep whose overall hit rate doesn't clear the noise floor. Worth
retesting with more Kite-tradable commodities/FX pairs specifically (the
instrument class `CL=F` belongs to) before calling this "found" — flagged
for a future session rather than ported into the tested `daily_strategy.py`
architecture yet, same treatment IBS and gap-fill received while still
unconfirmed.

## Eighteenth: SuperTrend retested on 8 more commodities/FX pairs — zero survivors, CL=F confirmed as a lucky draw

Direct follow-up on the Seventeenth entry's own flagged next step: retested
`probe_supertrend.py` (unchanged, default `st_period=10`/`multiplier=3.0`) on
the 8 Kite-tradable commodities/FX pairs not in the original 12-instrument
screen — the same set the Donchian section above used to extend its own
search (`NG=F`, `HG=F`, `SI=F`, `PL=F` on MCX; `USDINR=X`, `EURINR=X`,
`GBPINR=X`, `JPYINR=X` on the currency segment) — specifically because
`CL=F` (oil) is itself an MCX commodity and this instrument class was the
natural place to look for corroboration.

**Result: 0/8 passed** (both walk-forward halves positive, no
drawdown-halt). 6/8 were consistently negative on both halves (`HG=F`,
`SI=F`, `PL=F`, `USDINR=X`, `EURINR=X`, `JPYINR=X` — `PL=F` also
drawdown-halted in-sample); 2/8 sign-flipped between halves (`NG=F`,
`GBPINR=X`) — the same "looks different on each half" pattern this file has
repeatedly distrusted, not a pass.

**Net verdict**: this directly answers the Seventeenth entry's open
question, and the answer is negative. Combined with the original 12, the
running SuperTrend hit rate is now 4/20 (20%) — still chance-level — but
more specifically, the 8 fresh instruments most similar to `CL=F` in kind
(commodities and FX, the exact class it belongs to) went 0/8. `CL=F` isn't
corroborated by nearby instruments the way a real cross-instrument mechanism
would be; it was the one lucky draw its own chance-level screen already
implied might exist. No further instruments obviously remain to retest
under this specific reasoning — closing this line rather than continuing to
sweep one instrument at a time. A genuinely different construction (not
another instrument swap on an existing one) is the next useful step if
continuing this project, per the same "no further instruments remain"
conclusion the Donchian FX/MCX extension reached earlier.

## Nineteenth: gold/silver ratio mean reversion — no gross edge at all, not even a cost-drag story

A genuinely different construction per the Eighteenth entry's own
conclusion, not another instrument swap on an existing mechanism: the
gold/silver ratio, a real, widely-followed commodity pairs trade (unlike
the equity pairs trading already killed on magnitude in the Fifth entry).
Premise: gold and silver are economically linked (both precious/industrial
hedges), so their PRICE RATIO should mean-revert even while either metal
trends on its own — and both legs (`GC=F`, `SI=F`) have independently shown
a real, sizing-responsive edge before in this project (IBS's gold survivor,
SuperTrend's oil survivor), making the pair worth testing beyond each leg
alone.

Implemented as `probe_gold_silver_ratio.py`, standalone (same reasoning as
every other probe here — no natural fit for `daily_strategy.py`'s
single-instrument `check_entry(close)` interface). Method: `ratio =
gold_close / silver_close`, z-scored over a rolling window (mean/stdev of
the ratio itself — no OLS hedge-ratio regression needed since the ratio IS
the spread, unlike the equity pairs trade's two-stock hedge fit); \|z\| >
`entry_z` arms a trade (short gold/long silver if the ratio's too high,
long gold/short silver if too low), exits at \|z\| < `exit_z` or a
`max_hold_days` timeout. Equal notional both legs (not hedge-ratio-
weighted) — matches how this specific ratio is actually traded in
practice, not an equity-pairs-style regression fit.

**Result: 0/18 parameter configs passed** (`window` in {20, 30, 40, 60, 90,
120} x `entry_z` in {1.5, 2.0, 2.5}, all walk-forward both-halves-positive
screens) — every single cell negative on BOTH halves, no sign flips at all
(the cleanest possible negative — not even the "looks good on one half"
pattern that at least suggests a signal worth investigating further).

**The failure mode is different from, and more clean-cut than, every
prior cost-driven failure in this file** (SI=F's silver scalping, the
overnight-drift trade): those had a real, sometimes large, GROSS edge that
transaction costs wiped out. Here the gross P&L itself is already
negligible and mixed-sign across every config (e.g. `window=60,
entry_z=2.0`: gross -200/+137 across the two halves) — commissions turn a
roughly-zero gross result slightly negative, but there was no real gross
edge being masked in the first place. The gold/silver ratio just doesn't
move far enough, or predictably enough, for this mean-reversion
construction to find anything, at any of the 18 tested parameter
combinations.

**Net verdict**: nineteenth mechanism, and the least ambiguous failure of
the whole project — no cliff to investigate, no single surviving instrument
to chase, no cost-drag story to try to fix with lower frequency, just a
flat zero-edge result across the entire tested parameter grid. Both legs
individually showing a thin-but-real edge before (gold via IBS, oil via
SuperTrend) didn't imply their RATIO would too — confirms these are
correlated but not so tightly co-integrated that a simple ratio z-score
finds a tradable mean-reversion pattern. Not investigated further (no
perturbation of exit_z/max_hold_days/commission attempted, since gross
P&L near zero everywhere means no config is close to a pass) — this
result is clean enough not to need it.

## Twentieth: Turtle Soup (failed-breakout fade) — chance-level hit rate again, but the cleanest single-survivor perturbation sweep in the project

A genuinely different premise from everything else in this file, not
another instrument or parameter swap: Linda Raschke's "Turtle Soup"
(Raschke & Connors, "Street Smarts") FADES a new N-day breakout instead of
trading with it — the exact counter-trend inverse of
`DonchianBreakoutStrategy`'s own entry trigger. Premise: a fresh N-day
high/low is often a stop-hunt that fails to hold, not the start of a real
trend, so buy a failed new low and sell a failed new high once price closes
back inside the prior range.

Implemented as `TurtleSoupStrategy` in `daily_strategy.py`
(`--strategy turtlesoup` in `backtest_daily.py`) — ported straight into the
tested architecture rather than a standalone probe, since it fits the
shared `push`/`check_entry`/`check_exit` interface cleanly (checked one bar
in arrears using only already-`push()`'d history, same no-lookahead
convention as Donchian: at check time, `self._highs/_lows/_closes` hold
everything through yesterday, and `prior_high`/`prior_low` are computed
excluding yesterday itself via `highest(self._highs[:-1], channel_period)`).
Same ATR-generalized stop/target convention as `ThreeBarBreakoutStrategy`
(`stop_buffer_atr_mult`, `target_r_multiple`, fixed R-multiple exit) but a
shorter default `max_hold_days` (5, not 20) to match Raschke's own
short-holding-period rule. 8 new unit tests; full suite 110/110 green.
**A real, pre-existing bug was also found and fixed while adding these
tests**: four test function names in `tests/test_daily_strategy.py` were
duplicated across different strategies' sections (Python keeps only the
LAST definition of a duplicate name, so earlier same-named tests were
silently never executing) — `test_no_signal_before_window_fills` was
defined 3 times (Donchian/Squeeze/Volume, only Volume's ever ran) and 3 of
my own new test names collided with `ThreeBarBreakoutStrategy`'s. All
renamed to be unique; the true count of independent tests that actually
execute is now verified, not just assumed from the "N passed" total.

**Screening result: 3/12 passed** (both halves positive, no drawdown-halt,
this file's actual standard — not just same-sign, which `^NSEBANK`,
`TCS.NS`, `INFY.NS`, and `WIPRO.NS` also hit but both-NEGATIVE, a
consistent loser rather than a pass) — `^NSEI`, `ITC.NS`, `AXISBANK.NS`. A
25% hit rate, the same chance-level band as Squeeze (25%) and volume (30%).

**Quarter-split separates the three** the same way it always has in this
file: `^NSEI` and `ITC.NS` both have 2 of 4 quarters negative (though in
both cases Q4, the most recent, is the strongest — no decay red flag,
just noisier). `AXISBANK.NS` is the cleanest: **all 4 quarters positive**
(+2,957 / +1,856 / +1,574 / +195), decelerating toward the most recent
quarter but never negative.

**`AXISBANK.NS`'s perturbation sweep is the smoothest single-survivor
result in the entire project** — `channel_period` from 10 to 30 (5 values)
and `target_r_multiple` from 1.0 to 3.0 (5 values) are BOTH positive on
BOTH walk-forward halves at every single tested value, no cliffs anywhere.
Notably this is the same instrument (`AXISBANK.NS`) that failed
SuperTrend's perturbation sweep specifically for a single-point-fit cliff
at its exact default `st_period` — a different mechanism on the same stock
showing genuine robustness where another one showed curve-fit fragility is
a useful confirmation that this isn't just "AXISBANK.NS is an easy stock to
overfit to."

**Sizing helps, but only up to a point, the same "1% is close to the
safe ceiling" pattern as 3-bar breakout/Squeeze/SuperTrend**: 0.5%
risk-per-trade gives 0.81%/year (0.6% max DD); 1% gives **1.71%/year**
(1.1% DD) — genuine roughly-linear scaling. 2% breaks it (drawdown-halted
at 11.7% DD, trade count collapsing from 69 to 13); 3% is also halted.

**Net verdict**: twentieth mechanism, and — like SuperTrend before it —
a chance-level (25%) initial hit rate with one instrument surviving every
subsequent rigor check about as cleanly as this project's best individual
results (RSI-2, 3-bar breakout). But the magnitude at the safe sizing tier
(1.71%/year at 1% risk) is thin, in the same bucket as SuperTrend's `CL=F`
and volume's `GC=F` rather than 3-bar breakout's stronger 2-4.6%/year — and
per this file's own established standard (see the SuperTrend/Eighteenth
entries), a single-instrument survivor from a chance-level sweep isn't
independently confirmed until retested against more instruments of a
similar kind. Not done in this session — flagged the same way IBS's and
SuperTrend's lone survivors were, rather than claimed as "found."

## Twenty-first: MACD crossover — the lowest hit rate of any indicator-based strategy tried, one thin survivor

Gerald Appel's MACD (Moving Average Convergence/Divergence) — one of the
most widely used technical indicators that exists, and notably the first
MOMENTUM-OF-A-TREND mechanism in this file rather than a channel breakout
(Donchian, SuperTrend), a level oscillator (RSI-2), a fixed pattern (3-bar
breakout, Turtle Soup), or a volatility-state signal (Squeeze). Rule:
`macd_line = EMA(fast) - EMA(slow)` of closes, `signal_line =
EMA(signal_period)` of the macd_line itself; buy when macd crosses above
signal, short when it crosses below. Classic defaults 12/26/9.

Implemented as `MACDStrategy` in `daily_strategy.py` (`--strategy macd` in
`backtest_daily.py`). Required a new indicator primitive,
`indicators.ema_update()` — unlike every other indicator in this file
(rsi/average_true_range/stdev/linreg), which recompute fresh from a bounded
trailing window every call, an EMA's whole point is that older bars never
fully drop out, so recomputing it from a window each call would silently
be a DIFFERENT indicator. `MACDStrategy` therefore keeps running EMA state
(`_ema_fast`, `_ema_slow`, `_macd_ema`) updated once per `push()`, a
deliberate departure from this file's usual windowed-recompute style.
`check_entry`/`check_exit` need TWO consecutive points to detect a
crossover, but `push()` for today hasn't run yet when they're called (same
no-lookahead convention as everywhere else) — a private `_project(close)`
computes what today's macd/signal WOULD be via `ema_update()` without
mutating stored state, the same "fold today's own close into the oscillator
without lookahead" trick `ConnorsRSI2Strategy` already established. 10 new
unit tests; full suite 118/118 green.

**Screening result: 2/12 passed** (both halves positive, no drawdown-halt)
— `TCS.NS`, `WIPRO.NS`. A 16.7% hit rate, the lowest of any indicator-based
strategy in this project (below Squeeze's 25%, volume's 30%, Turtle Soup's
25% — closer to the sector sweep's already-distrusted 18% floor).
`AXISBANK.NS` is worth flagging separately: its in-sample half
drawdown-halted at -10,126 net P&L (10.1% drawdown) while its
out-of-sample half was healthily positive (+3,672) — a reminder (per this
file's own walk-forward blind-spot note) that a "CONSISTENT"-looking or
even inconsistent-but-eye-catching result needs the halt flag checked
before reading anything into it.

**Quarter-split separates the two**: `WIPRO.NS` shows the now-familiar
recent-quarter-decay signature — Q1-Q3 strongly positive but **Q4
(2024-2026, the most recent) is negative** (-202). `TCS.NS` is cleaner: all
4 quarters positive (+5,121 / +3,191 / +152 / +522) — decelerating toward
the present but never negative, no decay red flag.

**`TCS.NS`'s perturbation sweep is reasonably robust, not a single-point
fit**: `signal_period` (5, 7, 9, 12, 15) is smooth and monotonic —
all-positive on both halves at every tested value, improving as the signal
line slows down. `fast_period`/`slow_period` pairs are messier — 5 of 7
tested pairs pass (6/13, 12/26 default, 16/35, 20/43, 24/52) but two
middling pairs fail (8/17, 10/21) — not a hard cliff at the exact default
(both faster AND much slower variants than 8/17-10/21 still pass), more a
soft dip in the middle of the tested range.

**Sizing helps up to a point, the same "1% is near the ceiling" pattern
already seen repeatedly in this file**: 0.5% risk-per-trade gives
0.74%/year (1.5% max DD); 1% gives **1.73%/year** (2.6% DD) — genuine
roughly-linear scaling. 2% breaks it (drawdown-halted at 11.2% DD, trade
count collapsing from 106 to 41); 3% is also halted.

**Net verdict**: twenty-first mechanism, and by hit rate the weakest
initial screen of any indicator-based strategy tried here — yet the one
survivor that matters (`TCS.NS`) clears quarter-split cleanly and shows
real (if imperfect) perturbation robustness, landing in the same "real but
thin" bucket as Turtle Soup/SuperTrend/volume's lone survivors (~1.7%/year
at safe sizing) rather than either a clean pass or a clean failure. One of
the most famous indicators in retail technical analysis turning in one of
the weakest hit rates in this project is itself informative — popularity
and public familiarity (everyone's broker platform has a MACD indicator)
evidently isn't correlated with this project's rigor bar.

## Twenty-second: Trend + Volume-Confirmed IBS Reversion — an ORIGINAL strategy, and a clean test of whether stacking independently-real signals compounds edges (it doesn't)

The first mechanism in this project NOT sourced from a forum thread or an
open-source repo — a hypothesis built directly from three of this
project's own prior findings, combined explicitly rather than cited:

1. IBS mean reversion (Thirteenth) found exactly one survivor (`GC=F`) out
   of 8 instruments — 12.5%, at/below chance level — trading every IBS
   extreme with no context about trend or real money flow behind it.
2. RSI-2 (Third) found its SMA trend filter was the most theoretically
   coherent, robust result in the project — the edge held up specifically
   BECAUSE trades were filtered to align with the prevailing direction.
3. CMF volume confirmation (Sixteenth) found its own one clean `GC=F`
   survivor — the SAME instrument IBS's lone survivor was.

**Hypothesis**: an unfiltered IBS extreme is often either a falling knife
or a low-conviction wiggle; requiring it to occur WITH the trend AND with
money already flowing in (CMF > 0) should filter out both failure modes
and might extend IBS's real-but-narrow edge beyond the one instrument it
found alone. This is a genuine three-way ENTRY-side confirmation — a
different claim from the Eleventh entry's finding that EXIT-side stacking
(an ATR trailing-profit overlay bolted onto RSI-2's own exit) actively
hurts; entry-side AND-confirmation hadn't been tested.

Implemented as `probe_trend_volume_ibs.py` (same shape-mismatch reasoning
as `probe_ibs.py` for not porting into `daily_strategy.py` — IBS needs
today's own high/low at the entry decision). `IBS` itself was promoted
from a private helper duplicated inside `probe_ibs.py` to a shared
`indicators.internal_bar_strength()`, since a second caller now needed it
— `probe_ibs.py` refactored to import it rather than keep its own copy.
Also added direct unit tests for `internal_bar_strength()` and
`ema_update()` (the MACD primitive from the Twenty-first entry, which only
had indirect coverage via `MACDStrategy`'s own tests until now) — full
suite 124/124 green.

**Result: 0/10 instruments passed** walk-forward (both halves positive) —
worse than IBS alone (1/8, 12.5%) or CMF-volume alone (3/10, 30%). Most
strikingly, **`GC=F` — the one instrument where BOTH ingredient strategies
individually had a clean, real survivor — fails when combined**: in-sample
negative, out-of-sample marginally positive, inconsistent. A follow-up
10-config perturbation on `GC=F` specifically (`trend_period` 20-150,
`ibs_entry_long` 0.1-0.3) found the same inconsistent pattern at every
single tested value — not a default-parameter artifact, a genuine null
result across the whole tested space.

**Net verdict**: the hypothesis is cleanly refuted, and the way it fails is
informative. Combining three independently-plausible signals via AND-logic
didn't compound their edges — on the one instrument most likely to benefit,
it destroyed a working pair of independent edges instead. The likely
mechanism: each individual filter (trend, CMF sign) is itself noisy at
daily resolution, and requiring several noisy conditions to align
simultaneously shrinks the sample toward statistical noise faster than it
concentrates toward "quality" setups — plus a strong IBS dip WITHIN an
already-confirmed uptrend may just be a different (and not necessarily
better) market event than an IBS dip on its own. Combined with the
Eleventh entry's exit-side finding, this project now has two independent,
differently-constructed tests of "stack multiple real signals together for
a stronger edge," and both went the wrong direction — a real, generalizable
lesson for evaluating future multi-factor ideas here: prefer testing one
NEW mechanism over combining several already-tested ones, unless there's a
specific causal story for why the combination should behave differently
than either signal alone (this one had a story, and it still didn't hold).

## Twenty-third: does Carver-style volatility sizing rescue the "breaks at 2% risk" ceiling? No — and the reason why is the real finding

Not a new entry/exit mechanism, but a targeted investigation into WHY so
many strategies in this file hit the same wall: `RiskManager
.volatility_position_size()` (Carver-style ATR-based sizing) has existed
in `risk.py` since early in this project, is unit-tested, but was never
actually wired into a backtest — exactly the kind of already-in-the-
codebase tool the "reuse before you write" principle points at.

**Hypothesis**: 3-bar breakout, Squeeze, SuperTrend, Turtle Soup, and MACD
all show the same shape — sizing genuinely helps from 0.5% to 1% risk, then
drawdown-halts at 2%+. Maybe this isn't because the edge itself is too
thin, but because stop-based sizing (`risk.position_size()`, quantity =
risk_amount / stop_distance) occasionally produces an outsized position
when a particular trade's stop happens to sit unusually CLOSE to entry
relative to the instrument's typical daily range — a single oversized
trade, not a systemic problem. Capping quantity by the instrument's own
ATR (`min(stop_qty, vol_qty)`, exactly as `volatility_position_size()`'s
own docstring recommends using it) should catch that specific case without
touching any entry/exit logic.

Wired in as `vol_size_cap` (`simulate_daily()` parameter, `--vol-size-cap`
CLI flag) — opt-in, defaults preserve old behavior exactly, gated on the
same `current_atr()` strategies already used by the profit-booking
overlay. 2 new unit tests (the cap binding on an artificially tight stop,
and the off-by-default case).

**Result: no meaningful difference on any strategy tested, at the exact
risk levels that previously drawdown-halted** — Turtle Soup/`AXISBANK.NS`
@2% (11.7%→11.4% drawdown, still halted), MACD/`TCS.NS` @2% (bitwise
IDENTICAL trades and P&L — the cap never bound at all), 3-bar
breakout/`SBIN.NS` and `INFY.NS` @2% (both still halted, P&L barely
moved). Every case: still drawdown-halted, same trade count, marginal P&L
shift at best.

**Why it doesn't bind, and what that reveals**: MACD's stop is defined as
`stop_atr_multiple(2.0) x ATR` directly — a PURE ATR multiple — so
`stop_qty = risk/(2×ATR)` is mathematically ALREADY ≤ `vol_qty =
risk/ATR` for any multiple ≥ 1. The same is true for RSI-2 (multiple 3.0),
Squeeze and volume (2.0 each) — this project's own convention of scaling
every structural-less stop by "N x ATR" (adopted specifically to avoid the
forex-pip bug documented earlier in this file) already makes a SEPARATE
ATR cap redundant by construction for most strategies here. For the
structural-stop family (Donchian, 3-bar breakout, Turtle Soup — stop
derived from a channel/compression/failed-extreme level, only loosely
ATR-related), the cap occasionally binds (small P&L differences on
`INFY.NS`) but never enough to avoid the drawdown-halt, because the halt
isn't being caused by one-off oversized trades in the first place — it's
that raising `risk_per_trade_pct` uniformly scales EVERY position's size
together, so an ordinary STRING of losing trades (not one outlier) burns
through the fixed 10% drawdown ceiling faster. That's a frequency/variance
problem, not a per-trade-sizing-outlier problem, and no per-trade cap can
fix it.

**Net verdict**: this is a null result for the position-sizing question
specifically, but a genuinely useful mechanistic one — it rules out "smarter
single-trade sizing" as a lever for pushing this project's real-but-thin
survivors past their current ~1% safe-risk ceiling, and explains precisely
why: the ceiling is a return-variance-vs-fixed-drawdown-limit problem, not
a sizing-outlier bug, so it can only be moved by finding an edge with a
higher underlying Sharpe ratio (fewer, more consistent wins relative to its
own volatility) — not by refining how any single position gets sized. Left
wired in as `--vol-size-cap` (harmless, off by default) since the
underlying tool is now genuinely tested and available for a future
strategy whose stop ISN'T already ATR-scaled, where it could still matter.

## Twenty-fourth: Low-Volatility Anomaly — real over the full period, but the SAME recent-quarter decay now seen a third independent time

A real, decades-documented academic factor sourced properly (not an
original design this time): Frazzini & Pedersen's "Betting Against Beta"
(2014) and Ang/Hodrick/Xing/Zhang's "The Cross-Section of Volatility and
Expected Returns" (2006) — low-volatility/low-beta stocks earning HIGHER
risk-adjusted returns than high-beta ones, the opposite of CAPM,
attributed to leverage-constrained investors overpaying for high-beta
stocks chasing return without margin. Genuinely different from momentum
rotation (Fourth entry): ranks stocks by trailing VOLATILITY (a risk
measure), not trailing RETURN (a momentum measure) — a risk-mispricing
story, not price extrapolation.

Implemented as `probe_low_volatility.py`, reusing momentum rotation's own
monthly-rebalance architecture and realistic low-capital equity cost model
(0.2% STT+stamp both legs, ~₹16 DP charge on the sell leg only, zero
delivery brokerage) for direct comparability — rank a 20-stock NSE
large/mid-cap universe by trailing `lookback_days` daily-return stdev,
equal-weight the `top_n` least volatile, rebalance monthly. Same
survivorship-bias caveat as momentum rotation: today's well-known
large/mid-caps, not a point-in-time historical constituent list.

**Screening result: passes on the surface** — full 10y period +85.2%
total return, both walk-forward halves positive (13.12%/yr in-sample,
3.16%/yr out-of-sample, CONSISTENT), and an 8-config perturbation sweep
(`top_n` 3/5/7/10, `lookback_days` 126/189/252/378) is positive on BOTH
halves at every single tested value — genuinely smooth, no cliffs.

**Quarter-split reveals the exact same decay pattern already seen twice in
this file** (Donchian/BTC-USD's historic-bull-run capture, momentum
rotation's Fourth entry) — **and it survives the SAME perturbation
sweep that looked clean above**: default config Q1-Q3 are strongly
positive (+6.92%, +29.18%, +10.52%/yr) but **Q4 (2024-2026, the most
recent and most relevant window) is -4.77%/yr**. Re-checked on the two
strongest-looking perturbed configs specifically (`top_n=10`:
+10.25%/+34.34%/+10.60%/**-1.94%**; `lookback_days=126`:
+14.31%/+16.35%/+12.00%/**-3.26%**) — Q4 negative in every single
config tested, not a default-parameter artifact. This is precisely the
"walk-forward blind spot" this file has warned about before: the 2-way
split's out-of-sample half (which spans Q3+Q4) still nets positive because
Q3's gain outweighs Q4's loss, completely hiding the decay a finer split
catches — exactly why this project's screening standard has never trusted
a 2-way split alone.

**A sharper detail than the prior two decay cases**: Q4's underperformance
isn't just riding a falling market down — the equal-weight FULL UNIVERSE
benchmark was actually **+6.3% over the same Q4 window** while the
low-volatility basket lost money. That's genuine negative alpha in the
most recent regime, not beta exposure to a down market — consistent with
a well-known real characteristic of the low-vol factor: it structurally
underperforms during beta-chasing/momentum-driven rallies (lower-beta
stocks by construction lag when riskier names are being bid up), which is
plausibly what 2024-2026's NSE rally looked like for this specific
universe.

**Net verdict**: twenty-fourth mechanism, and the third INDEPENDENT
instance (trend-following on crypto, cross-sectional momentum, now a
risk-based factor) of this exact same failure mode in this project — a
real multi-year effect that reverses or vanishes in the ~2.5-year window
closest to now. Three different mechanisms hitting the identical pattern
in the identical recent window is itself the more important finding than
any one of them individually: it suggests 2024-2026 has been a genuinely
unfavorable regime for systematic equity strategies broadly on this NSE
universe (a market-wide regime characteristic, not a flaw specific to any
one mechanism) rather than three coincidentally-timed individual failures.
Any future strategy candidate showing "great full-period, both-halves-
positive" results should be treated with active suspicion until its own
Q4 is checked — this project's default now, not an occasional extra step.

## Cross-mechanism synthesis: a real regime bifurcation, only visible by comparing entries against each other

Not a new mechanism — a pattern only visible by reading every prior
quarter-split result side by side, which no single entry above was
positioned to notice on its own. Compiled after the Twenty-fourth entry
made a third instance of the same decay:

**Q4 2024-2026 (the most recent, most relevant window) has been NEGATIVE
or decaying for every broad, multi-instrument, basket-style equity/crypto
DIRECTIONAL bet tested**: Donchian trend-following on BTC-USD/ETH-USD
(essentially flat, ~-0.05%), cross-sectional momentum rotation across 25
NSE stocks (-7.1% CAGR), the low-volatility factor across 20 NSE stocks
(negative at every tested config, while the equal-weight universe
benchmark was actually +6.3% over the same window).

**Q4 2024-2026 has been POSITIVE — often the single BEST quarter — for
every single-instrument, short-horizon, mean-reversion-or-pattern-based
mechanism tested, regardless of asset class**: options premium selling
(both synthetic and real-data-validated — Q4 the best window, not the
worst), IBS mean reversion on gold (Q4 its best quarter), CMF+OBV volume
confirmation on gold (Q4 its strongest quarter), SuperTrend trend-following
on oil (Q4 the strongest, no decay), Turtle Soup's failed-breakout fade on
`AXISBANK.NS`/`^NSEI`/`ITC.NS` (Q4 strongest or stable), 3-bar breakout's
five non-anomalous survivors (no recent-quarter-is-worst pattern), MACD on
`TCS.NS` (Q4 positive, if decelerating).

**Read together, not asset class, but BET STRUCTURE is what predicts
Q4 behavior in this dataset**: a strategy making one basket-wide
directional bet across many correlated equities/crypto at once has done
badly in the most recent window; a strategy making narrow, mean-reverting
or short-horizon bets on ONE instrument at a time — commodity, stock, or
index — has done fine or well. The plausible causal story: broad factor/
trend bets profit from a sustained, correlated move across many names at
once (which 2019-2024 provided and 2024-2026 evidently hasn't), while
single-instrument mean reversion profits FROM choppiness and reversal,
which is what a regime transitioning out of a sustained rally would
produce more of. This is a hypothesis about THIS dataset's most recent
window, not a permanent law — but it's now been seen independently across
seven-plus different mechanisms and is too consistent to be coincidence.

**How to apply going forward**: this project should stop testing more
broad, basket-wide equity/crypto factor rotations for now (two real
academic factors — momentum, low-volatility — have both hit this same
wall) and prioritize single-instrument, short-horizon candidates,
especially ones that can be validated as currently favorable rather than
just historically real. It also surfaces a concrete unfinished thread:
unlike SuperTrend (retested against 8 more commodities/FX pairs in the
Eighteenth entry after its lone survivor was found), IBS's Thirteenth
entry flagged the identical "retest with more instruments" next step and
it was never actually done — worth doing now, both on its own merits and
because this synthesis raises the prior that a commodity/short-horizon
retest might land differently than SuperTrend's did.

## Twenty-fifth: the IBS/FX retest looked spectacular, then a lot-size check found the same capital wall that closed out options — and revealed a systemic gap across every commodity/FX result in this project

Direct follow-up on the synthesis above: retested `probe_ibs.py` on the
same 8 Kite-tradable commodities/FX pairs used for the SuperTrend and
Donchian extensions (`NG=F`, `HG=F`, `SI=F`, `PL=F` on MCX; `USDINR=X`,
`EURINR=X`, `GBPINR=X`, `JPYINR=X` on the currency segment).

**Initial result looked like the best number in the whole project**: all
4 currency pairs passed walk-forward cleanly — `USDINR=X` +7.99%/+6.18%,
`EURINR=X` +7.91%/+8.42%, `GBPINR=X` +8.18%/+8.56%, `JPYINR=X`
+9.37%/+11.28% (both halves, 275-337 trades each) — with drawdowns under
1% across the board. 4/4 FX pairs passing, at magnitudes far bigger than
anything else in this project, would have been the single strongest
result found here.

**It doesn't survive the very next check, and the reason is important
beyond just this one strategy**: `probe_ibs.py` (like every probe script
and `backtest_daily.py`'s `RiskManager` calls in this entire project) sizes
positions as `risk_amount / stop_distance` with NO connection to the
instrument's actual exchange lot size — it implicitly assumes you can buy
or sell any continuous fractional quantity, which is true for equity
shares (Zerodha lets you buy 1 share) but NOT true for currency or
commodity DERIVATIVES, which only trade in fixed contract lots. Checked
directly for `USDINR=X`: mean 14-day ATR over the 10y window is ≈₹0.50,
so IBS's default 3×ATR stop is ≈₹1.50. A real USDINR futures lot is
$1,000 notional (needs verification against current Kite contract specs,
not independently confirmed here) — so ONE lot's risk at that stop is
₹1,000 × ₹1.50 ≈ **₹1,496 per trade**. Keeping that within a sane 0.5%
risk-per-trade standard needs **≈₹299,000 of capital just to size ONE
lot** — this is structurally the identical capital-tier wall the
Seventh/Ninth entries found for Nifty options (₹350,000-475,000 needed),
not a newly-found low-capital edge. The eye-catching backtest numbers were
a mathematical artifact of assuming continuous position sizes a real Kite
account can't actually take.

**This is a systemic gap, not specific to this one test**: the exact same
lot-size-blind sizing has been used for EVERY commodity/FX result reported
in this project so far, including three previously-reported "real"
survivors — SuperTrend's `CL=F` (Seventeenth), volume/CMF+OBV's `GC=F`
(Sixteenth), and IBS's own original `GC=F` survivor (Thirteenth). None of
those have been checked against real MCX contract lot sizes (further
complicated there by the already-flagged currency/unit mismatch — `GC=F`
is a USD/troy-ounce COMEX price used directly against rupee capital, a
simplification this project has acknowledged before but never combined
with a lot-size check). **Not resolved in this session** for lack of
confirmed current MCX/currency contract specifications — flagged
explicitly as an open validation gap that should be checked before trusting
ANY of this project's commodity/FX "survivors" at face value, the same
way the options line's capital-tier arithmetic was checked before trusting
its headline CAGR.

**Net verdict**: twenty-fifth entry, and a genuinely important methodological
catch rather than a new tradable mechanism — this project's rigor checklist
(walk-forward, quarter-split, perturbation, transaction costs) has never
included "is the implied position size an integer number of real exchange
lots," and this is the first time that gap produced a result striking
enough to demand investigating it directly. The FX/IBS numbers are retracted
as unvalidated, not reported as a finding. The right next step, if
continuing this line, is a lot-size-correct re-verification of the
project's existing commodity "survivors" (`CL=F` SuperTrend, `GC=F`
volume/IBS) before adding anything new on top of them.

## Twenty-sixth: doing the lot-size check the Twenty-fifth entry recommended — gold's standard contract and oil (even Mini) are blocked, but gold's smallest denomination might not be

Direct follow-up: checked whether `CL=F` (SuperTrend's oil survivor) and
`GC=F` (IBS's and volume's gold survivors) correspond to a sensible number
of real MCX contract lots, using each instrument's own mean 14-day ATR and
IBS's `stop_atr_multiple=3.0` convention as a representative stop size.

**A methodological point that mattered more than expected**: this
calculation has to apply a REAL USD/INR conversion (`GC=F`/`CL=F` are
USD-denominated global prices; MCX contracts settle in rupees), NOT this
project's existing "treat the USD figure as if it were rupees" convention
used throughout every prior GC=F/CL=F backtest (itself an already-flagged,
deliberate simplification — see the SuperTrend entry's own docstring). The
two give answers roughly 88x apart (at an assumed USDINR≈88, itself an
approximation needing live verification) — skipping the real conversion
would have been badly misleading here, in the optimistic direction.

**Results** (mean ATR: gold $34.49/oz, oil $2.49/bbl; stop = 3×ATR;
lot sizes are recalled/assumed figures needing verification against
current live Kite/MCX contract specs, not independently confirmed):

| Contract | Risk/lot | Capital needed @0.5% | @1% | @2% |
|---|---|---|---|---|
| Gold 1kg (standard) | ₹292,752 | ₹58,550,478 | ₹29,275,239 | ₹14,637,620 |
| Gold 100g (Mini) | ₹29,275 | ₹5,855,048 | ₹2,927,524 | ₹1,463,762 |
| Gold 8g (Guinea) | ₹2,342 | ₹468,404 | ₹234,202 | ₹117,101 |
| **Gold 1g (Petal)** | **₹293** | **₹58,550** | **₹29,275** | **₹14,638** |
| Oil 100bbl (standard) | ₹65,630 | ₹13,126,080 | ₹6,563,040 | ₹3,281,520 |
| Oil 10bbl (Mini) | ₹6,563 | ₹1,312,608 | ₹656,304 | ₹328,152 |

**Gold's standard and Mini contracts, and BOTH oil contract sizes (even
Mini), are blocked at this project's target capital** — the same
capital-tier wall options-selling and FX hit, at similar or larger
magnitude. `CL=F`'s SuperTrend survivor specifically needs ₹328,152-
1,312,608 even at the smallest real crude contract — not accessible at
this project's stated ₹30,000-100,000 target under any tested risk
setting.

**Gold's smallest denomination (Petal, 1g) is the one exception**: at
₹58,550/29,275/14,638 capital for 0.5%/1%/2% risk-per-trade respectively,
this sits comfortably inside this project's target range — meaning IBS's
and volume's `GC=F` survivors are NOT automatically disqualified by the
capital-tier wall, unlike everything else checked in this entry and the
prior one, PROVIDED Gold Petal contracts are actually listed, liquid, and
tradable via Kite for the exact instrument these strategies traded (none
of that is verified here — flagged explicitly, not assumed).

**Net verdict**: twenty-sixth entry, and a genuinely mixed, more useful
result than a blanket wall — most of this project's commodity/FX
"survivors" (oil in both sizes, gold's larger contracts) join options and
FX behind the same capital-tier wall once real currency conversion is
applied properly. But gold specifically has an escape hatch the others
don't: its smallest MCX denomination is granular enough that this
project's low-capital framing might still apply to IBS's/volume's `GC=F`
findings — the one place in this whole capital-tier investigation where
the answer isn't simply "blocked." Confirming this needs a live check of
Kite's actual currently-listed MCX gold contracts (which sizes exist,
liquidity, whether `IBS`/`CMF+OBV` position sizes round to a sane number
of Petal lots) — the concrete next step if this line is continued, and the
only remaining open thread from the capital-tier question this project has
now applied to every real "survivor" found so far.

## Twenty-seventh: classic Bollinger Bands mean reversion — 0/12, the worst hit rate of any strategy tested here

John Bollinger's own trading rule, one of the most widely used bands in
technical analysis and — surprisingly, given how central it is — not yet
tried in this project: a rolling `period`-day SMA of closes ± `num_std`
standard deviations forms upper/lower bands; buy a close below the lower
band, short a close above the upper, exit back at the middle band (the SMA
itself). Genuinely different from every other mean-reversion mechanism
here: IBS is a same-day positional signal (today's close within today's
own high-low range); Squeeze bets on a breakout once BB contracts inside
Keltner Channels (a continuation signal, not reversion at all). This is a
pure multi-day statistical band on closing prices.

Implemented as `BollingerBandsStrategy` in `daily_strategy.py`
(`--strategy bollinger`), reusing `indicators.sma()`/`stdev()` directly
(already present for `SqueezeMomentumStrategy`'s own band-width
calculation). ATR-based stop (no natural structural stop, same situation
RSI-2/Squeeze/volume/MACD were in); exit at the middle band or
`max_hold_days`. 8 new unit tests; full suite 133/133 green.

**Result: 0/12 instruments passed** (both halves positive, no
drawdown-halt) — the worst hit rate of any strategy in this project,
below even MACD's 16.7% and the sector sweep's 18% "chance" floor. Two
instruments (`RELIANCE.NS`, `TCS.NS`) drawdown-halted outright in-sample.
No perturbation or quarter-split needed given how uniform the failure is
(consistent with the gold/silver ratio probe's own precedent for a result
this clean) — 7 of 12 were consistently negative on both halves, the
other 5 sign-flipped.

**Net verdict**: twenty-seventh mechanism, and the cleanest possible
negative result for one of the most famous bands in retail technical
analysis. The likely reason, consistent with a well-known real critique of
naive Bollinger mean reversion: fading a band touch with no trend
context fights genuine trends as often as it catches real reversals,
and on NSE large-caps/indices that showed real (if thin) trend-following
edges elsewhere in this project (Donchian, SuperTrend), a pure
counter-trend rule with no filter gets run over often enough to net
negative. The Twenty-second entry already tested whether bolting a trend
filter onto a same-day mean-reversion signal (IBS) helps, and found it
doesn't — this result is a second independent data point suggesting
naive band-touch mean reversion specifically needs more than this project
has found to make it work on these instruments, not just this one
construction.

## Twenty-eighth: does a trend-strength REGIME filter rescue Bollinger Bands, the way it made RSI-2 this project's most robust result? No

Direct follow-up on the Twenty-seventh entry's 0/12 washout, testing a
specific, well-motivated hypothesis rather than moving straight to a new
instrument or mechanism: RSI-2 (Third entry) is this project's most
theoretically coherent, robust result specifically BECAUSE it pairs a
mean-reversion trigger with a trend/regime filter ("only buy dips ABOVE
the long-term trend"). Bollinger Bands has no such filter at all — maybe
adding one rescues it the same way. Important distinction from the two
combinations this project has already found HURT (Eleventh's exit-side
ATR overlay, Twenty-second's three-way AND-confirmed entry): a single
regime GATE on one timing trigger is structurally what RSI-2 already does
successfully, not a second independent directional signal stacked on top.

Added `trend_filter_lookback`/`trend_filter_atr_mult` to
`BollingerBandsStrategy` (0 = disabled, no behavior change to the
Twenty-seventh entry's own tests) — only takes a band-touch entry when the
middle band itself hasn't drifted more than `trend_filter_atr_mult` x ATR
over the last `trend_filter_lookback` days, i.e. skip mean-reversion
trades while the market is genuinely trending rather than range-bound. 4
new unit tests (filter disabled by default, allows entry when range-bound,
blocks when trending, stays flat when there's not enough history to judge
the regime yet); full suite 137/137 green.

**Result: still 0/12 at the default filter tightness** (`--bb-trend-
filter-lookback 20 --bb-trend-filter-atr-mult 1.5`) — same outcome as the
unfiltered Twenty-seventh entry, though with a real secondary benefit:
zero drawdown-halts this time (the unfiltered version halted 2 of 12,
`RELIANCE.NS`/`TCS.NS`), so the filter is doing SOMETHING (avoiding the
worst whipsaw periods), just not enough to create a net edge. **Checked
4 tightness settings (0.5, 1.0, 1.5, 2.5) on a 5-instrument subset —
0/5 passed at every single value tested**, not a default-parameter
artifact.

**Net verdict**: twenty-eighth entry, and a clean refutation of a
specific, well-reasoned hypothesis rather than an unmotivated parameter
sweep. RSI-2's trend filter works because RSI-2's own entry mechanism
(2-period RSI extremes) is a REAL, robust signal that a regime mismatch
was actively hurting; Bollinger Bands' band-touch entry doesn't appear to
be a real signal on these instruments at all, filtered or not — there's
no working core mechanism for a regime gate to protect. This is a useful
general lesson beyond this one strategy: a context/regime filter can only
rescue a technique that has a real edge underneath being masked by
regime-inappropriate trades; it doesn't manufacture an edge that wasn't
there. Worth remembering before adding a filter to any future weak
candidate — check whether the RAW signal shows partial promise (a few
genuinely clean instruments, a coherent theoretical story) before assuming
a regime gate will help, the way it demonstrably did for RSI-2 and
demonstrably didn't for Bollinger Bands.

## Twenty-ninth: checking the Gold Petal contract with REAL Kite data — it doesn't exist on MCX, closing the one open thread from the capital-tier investigation

Direct resolution of the Twenty-sixth entry's open question, using real
data instead of recalled/assumed figures for the first time in this
investigation: Zerodha publishes a full, current instrument master at
`https://api.kite.trade/instruments` — a plain CSV download, **no
authentication needed** (unlike everything else Kite-related in this
project, which needs the daily 2FA login dance). Fetched it directly
(9.2MB, ~unknown row count but every tradable instrument across every
segment) and filtered to gold contracts.

**MCX (the exchange this project has always meant by "gold"/"oil")
genuinely offers exactly three gold futures products, confirmed from live
current contract listings with real expiries through 2027**: `GOLD`
(the standard, large contract), `GOLDM` (Mini), `GOLDGUINEA`. **No 1-gram
"Gold Petal" contract exists on MCX.** A `GOLD1G` (1g) and `GOLD10G` (10g)
symbol DO exist in Kite's instrument universe, but under a completely
different exchange code, `NCO` — not MCX. `NCO`'s other listings
(`PLATTSDATEDBRENTASSESS`, `NATURALGASHENRYHUB` — international reference
benchmarks, not domestic MCX products) suggest this is a different,
possibly international/specialized segment, not standard domestic retail
MCX access — genuinely unclear from this data alone, not assumed either
way. If continuing this line, the user's own Kite app is the way to
confirm whether `NCO` instruments are even visible/tradable on a standard
account — not something checkable from here.

**This closes the Twenty-sixth entry's "Gold Petal escape hatch"
hypothesis: false.** The smallest CONFIRMED real MCX gold contract is
`GOLDGUINEA`, not a 1g Petal — meaning the Twenty-sixth entry's Guinea-row
capital figures (₹468,404 needed at 0.5% risk, ₹117,101 at 2%) are the
real floor for gold on MCX, not the much smaller Petal figures. Gold
rejoins oil, FX, and options behind the same capital-tier wall — there is
no remaining commodity/FX "survivor" in this project that clears it.

**An important methodological caveat surfaced while checking this**: the
instrument master's own `lot_size` column is uniformly `1` for every MCX
futures contract checked (gold, oil, and everything else) — it encodes
minimum TRADABLE INCREMENT (whole lots only), not the contract's
underlying notional (grams/barrels/dollars per lot). So while this data
source definitively answers "which contracts exist" (settling the Gold
Petal question outright), it can NOT verify the specific notional-size
assumptions used throughout the Twenty-fifth/Twenty-sixth entries'
capital-requirement math (GOLDGUINEA=8g, GOLDM=100g, CRUDEOIL=100bbl,
CRUDEOILM=10bbl, USDINR=$1000) — those remain based on recalled general
knowledge of MCX/CDS contract specifications, not verified against this
or any other source in this session. `CRUDEOIL` and `CRUDEOILM` (Mini) and
`USDINR` futures were all confirmed to genuinely EXIST as named (real,
current, correctly-segmented instruments), which is reassuring, but their
exact underlying size per lot is a separate, still-open question a
future session could resolve via MCX's/NSE's own published contract
specification sheets (a different data source than this instrument
master).

**Net verdict**: twenty-ninth entry, and the definitive close to this
project's whole capital-tier investigation line (Seventh through
Twenty-sixth entries) — every real "survivor" found in this project
(options premium selling, SuperTrend's oil, IBS's and volume's gold) is
now confirmed blocked by a genuine capital-access wall at this project's
target scale, with no remaining unverified escape hatch. Worth recording
as a reusable capability for any future capital-tier question in this
project: `https://api.kite.trade/instruments` is a real, current, freely
fetchable source of truth for WHICH contracts exist and their tick
size/expiry/segment — genuinely useful, and something this project didn't
know was available without authentication until this entry.

## Thirtieth: Post-Earnings Announcement Drift (PEAD) — the strongest-looking aggregate screen since options-selling, but concentrated in a chance-level 3/20 subset once checked

Per the user's specific request to keep testing NSE EQUITY strategies
(sidesteps the whole capital-tier wall just closed out — Zerodha equity
delivery is share-by-share, no fixed-lot problem). A real,
decades-documented academic anomaly (Bernard & Thomas, 1989, replicated
across many markets) and genuinely different in KIND from everything else
in this project: EVENT-DRIVEN (a specific earnings date and surprise
magnitude matter), not a continuous technical signal computed from price
alone. Finding: stocks that beat earnings estimates keep drifting UP for
weeks afterward (the market underreacts to the surprise on the day
itself); misses keep drifting down.

Data: yfinance's `Ticker.earnings_dates` gives real per-company quarterly
EPS estimate/actual/surprise% — verified genuinely different across
`RELIANCE.NS`/`TCS.NS`/`INFY.NS`/`HDFCBANK.NS` (not generic placeholder
data), 479 real events across a 20-stock universe over ~6 years. Known
data caveat: dates are reported in a US Eastern offset rather than IST, a
real yfinance imprecision partly absorbed by reacting on the next trading
day rather than the announcement day itself (same no-lookahead convention
as every other strategy here).

Implemented as `probe_pead.py`: enter LONG (beat) / SHORT (miss) when
`|surprise%| >= surprise_threshold`, at the close of the first trading
day on/after the earnings date, hold a FIXED `hold_days` (PEAD is
measured as drift over a period in the literature, not a technical exit
condition). **A real methodological bug was caught and fixed before
trusting any result**: the first implementation sized each new signal as
a fixed % of CURRENT capital with no cap on concurrent positions — since
earnings cluster within each quarter's reporting season, checking showed
up to 13 positions open simultaneously at the strategy's own defaults,
130% of capital deployed at once, not a real account. Rewritten to
process entries/exits in true chronological order (exits before entries
on the same day, freeing capital first) and SKIP a signal outright if it
would exceed 100% capital deployed, rather than silently over-allocating.

**Initial screen at conservative defaults (5% surprise threshold, 20-day
hold) was negative and inconsistent** — the same shape as most of this
file's failures. But perturbation revealed a real, coherent pattern
matching the literature itself (PEAD is specifically strongest for LARGE
surprises over LONGER windows, not small ones over short ones): raising
the threshold and lengthening the hold both pushed toward consistency,
and **a 4x3 grid (`surprise_threshold` 10/15/20/25 x `hold_days`
40/60/80) passed BOTH walk-forward halves at all 12 tested
configurations** — the strongest aggregate screening result in this
project since options-selling.

**Deeper checks found the real story, at the representative
`surprise_threshold=15`/`hold_days=60` config**: even under the
corrected capital-constrained simulation, walk-forward is genuinely
consistent (in-sample +1.94%/yr, out-of-sample +2.63%/yr, 113 trades, 52%
win rate) and quarter-split shows **no recent-quarter decay** — all 4 real
quarters positive (+5,115 / +570 / +5,835 / +2,124), matching this
project's established "single-instrument mechanisms have been Q4-favorable"
pattern rather than the broad-factor decay pattern. But per-symbol
breakdown shows the result is **concentrated in 3 of 20 stocks**
(`TATASTEEL.NS`, `SUNPHARMA.NS`, `ONGC.NS` — win rates 64%/90%/83% across
14/10/12 trades each, genuinely distributed across many trades within
each stock, not single lucky outliers) — **excluding just those 3 stocks
flips the entire aggregate result negative** (-12,394 net, 41% win rate
on the remaining 17 stocks' 83 trades). A 3/20 (15%) "real" hit rate is at
or below this file's own established chance-level disqualifying threshold
(sector sweep's 18%, Squeeze's 25%).

**Net verdict**: thirtieth mechanism, and the clearest illustration yet in
this project of why a positive AGGREGATE pooled-instrument screen isn't
sufficient on its own — a result can pass walk-forward, quarter-split, AND
a 12-config perturbation sweep simultaneously while still being driven by
a chance-level subset of the instruments in the pool, something none of
those three checks individually catch (they all operate on the pooled
total, not per-instrument). Per this project's own established treatment
of similar findings (IBS's lone `GC=F` survivor, SuperTrend's lone `CL=F`
survivor before its own retest failed), the 3-stock PEAD pattern is
flagged, not claimed as found — real internals (broadly distributed win
rates, no single-trade dependency, no Q4 decay), but 3 survivors from a
20-stock sweep isn't independently confirmed. The capital-constrained
sizing fix is a genuine, reusable methodological improvement worth
keeping regardless of PEAD's own fate — any future event-driven or
multi-position strategy in this project should check concurrent capital
deployment the same way, not just per-trade sizing in isolation.

## Thirty-first: extending PEAD to 40 stocks — the pattern doesn't replicate, closing this line the same way SuperTrend's retest did

Direct follow-up on the Thirtieth entry's own flagged next step: doubled
`probe_pead.py`'s `UNIVERSE` from 20 to 40 stocks (added `HCLTECH.NS`,
`TECHM.NS`, `DRREDDY.NS`, `CIPLA.NS`, `DIVISLAB.NS`, `BRITANNIA.NS`,
`NESTLEIND.NS`, `TITAN.NS`, `BAJAJFINSV.NS`, `BAJAJ-AUTO.NS`,
`EICHERMOT.NS`, `HEROMOTOCO.NS`, `JSWSTEEL.NS`, `HINDALCO.NS`, `VEDL.NS`,
`COALINDIA.NS`, `POWERGRID.NS`, `BPCL.NS`, `GRASIM.NS`, `ADANIPORTS.NS` —
a broader sector mix, no PSU/cyclical filter, since the original 3
survivors (`TATASTEEL.NS`/`ONGC.NS` cyclical-PSU, `SUNPHARMA.NS`
defensive pharma) don't actually share a coherent sector story worth
targeting). All 20 new symbols had sufficient earnings-history coverage
(15-24 events each) to include.

**At the exact same representative config (`surprise_threshold=15`,
`hold_days=60`) that was CONSISTENT and positive on both halves at 20
stocks, walk-forward flips INCONSISTENT at 40 stocks**: in-sample
-1.03%/yr (net -5,457), out-of-sample +4.63%/yr (net +27,725). The cast
of "winning" stocks also shifted — new names (`VEDL.NS` +13,641,
`BPCL.NS` +10,839, `HINDALCO.NS` +6,136, `JSWSTEEL.NS` +3,579) became the
largest contributors, while `TATASTEEL.NS`, the single biggest winner in
the Thirtieth entry's 20-stock screen (+13,940), shrank to a near-
breakeven +377 — the specific subset "carrying" the result changes every
time the universe changes, the same instability signature this project
has repeatedly learned to distrust.

**The full 4x3 perturbation grid, re-run on 40 stocks, confirms this
wasn't a single-config fluke**: only **4/12 configs pass** (all at
`surprise_threshold >= 20` — `20/60`, `20/80`, `25/60`, `25/80`), down
from 12/12 at 20 stocks. Notably the out-of-sample half is positive in
literally every one of the 12 configs; it's specifically the in-sample
half that turns negative for most of them once the wider universe is
mixed in — suggesting the newly-added stocks behave differently across
the sample's two temporal halves than the original 20 did, diluting or
reversing what looked like a clean effect in the smaller set.

**Net verdict**: thirty-first entry, and a clean, decisive non-
replication — the same conclusion the Eighteenth entry reached for
SuperTrend's oil survivor (retested against more instruments, came back
0/8) and the mirror image of what a genuine broad effect should do
(extending the universe should, if anything, strengthen a real pattern
with more independent confirmation; here it weakened and reshuffled it).
PEAD is closed out for this project: real academic grounding, a coherent
theoretical story, and internals that looked clean at every check
performed on the narrow 20-stock universe — but it doesn't survive being
asked to generalize, the single most important test this project applies
to any promising-looking result. The capital-constrained sizing fix from
the Thirtieth entry remains a permanent, valuable improvement to this
project's event-driven backtesting method regardless.

## Thirty-second: combining five of this project's own real-but-thin survivors as a diversified portfolio — worse than just running the single best one alone

A genuinely different test from every prior entry: not a new signal, and
not stacking signals into one trade (already found twice to hurt, the
Eleventh/Twenty-second entries) — the standard, sound portfolio-
construction question this project had never actually asked: does
combining SEVERAL of its own already-validated "real, clears every rigor
check, just too thin alone" survivors, each running independently on its
own instrument, produce a better combined risk-adjusted return than any
one alone? Real quant funds routinely do exactly this — diversify capital
across many small, uncorrelated edges rather than searching for one big
one.

Five components, each this project's own best-known instrument/strategy
pairing: `rsi2`/`RELIANCE.NS` (Third entry), `threebar`/`SBIN.NS` and
`threebar`/`HDFCBANK.NS` (Twelfth entry's two strongest survivors),
`macd`/`TCS.NS` (Twenty-first), `turtlesoup`/`AXISBANK.NS` (Twentieth —
this project's smoothest perturbation sweep). Implemented as
`probe_portfolio_combo.py`, reusing `backtest_daily.py`'s `simulate_daily()`
UNMODIFIED for each component (equal capital slice, own independent
`RiskManager`/`PaperBroker`) via a new optional `dated_trades` side-channel
parameter added to that function (default `None`, zero behavior change —
2 new unit tests confirm) that records `(bar_date, pnl)` at the exact
moment each trade closes, letting every component's trades be merged into
one chronological, shared-capital equity curve afterward — a real,
non-approximated combined result, not five independent numbers eyeballed
side by side. (First attempt used `rsi2`/`^NSEI` per the Third entry's own
headline instrument — surfaced a real, if secondary, finding: Nifty's
absolute point-based ATR is too large for a small per-component capital
slice to afford even one tradable unit at, not a real issue for the index
alone at full capital but a reminder that any capital-SPLITTING scheme
needs to check each component can still actually trade at its reduced
allocation. Swapped to `RELIANCE.NS`, one of RSI-2's other real Third-entry
survivors.)

**Result: the combined portfolio underperforms the single best component
on BOTH dimensions**, not just diluted return. Combined: +0.30%/year at
4.1% max drawdown, walk-forward INCONSISTENT (in-sample +0.67%/yr,
out-of-sample -0.44%/yr). `turtlesoup`/`AXISBANK.NS` ALONE at full capital
(the same config already documented in the Twentieth entry): +1.71%/year
at only 1.1% max drawdown — meaningfully BETTER return AND lower
drawdown than the 5-way diversified combination.

**Why diversification didn't help here**: two compounding reasons, both
checkable directly from this project's own prior entries rather than
speculation. First, correlation — all five components are long-biased
directional strategies on Indian large-cap equities, which move together
on broad market-wide days; genuine diversification benefit needs return
streams that are actually uncorrelated, and these likely aren't enough.
Second, and more simply: RSI-2's own edge (0.04-0.25%/year at realistic
sizing, per the Third entry) is by far the thinnest of the five
components, and equal-weighting means it drags the average down alongside
MACD's similarly thin ~1.7%/year — diversification doesn't rescue a
combination if some of the components contribute almost nothing to begin
with, it just averages a strong contributor together with several weak
ones.

**Checked the obvious follow-up in the same session rather than leaving
it as a suggestion**: re-ran with ONLY the three strongest components
(both `threebar` survivors + `turtlesoup`/`AXISBANK.NS`, dropping RSI-2
and MACD entirely). **Still worse than the single best component alone**:
+0.59%/year at 5.4% max drawdown, walk-forward still INCONSISTENT — an
improvement over the 5-way mix's +0.30%/1.71% but nowhere near
`turtlesoup`/`AXISBANK.NS` solo's +1.71%/year at 1.1% drawdown. Each
component's own capital slice (₹33,749/33,543/38,646 from a ₹33,333
starting slice each) shows roughly proportional, not amplified, scaling —
no "free lunch" bonus from combining them, consistent with genuine
positive correlation between all-long-biased NSE equity strategies rather
than a data artifact of including the weaker components.

**Net verdict**: thirty-second entry, and a clean, well-reasoned negative
result for a genuinely untested question (portfolio combination, not
signal stacking) rather than a repeat of ground already covered, confirmed
robust to component selection rather than just one unfavorable mix. The
lesson generalizes usefully: this project's real-but-thin survivors don't
average up into something more substantial by simply running them
together, even restricted to the best-known ones — a real diversification
benefit needs genuinely uncorrelated return sources, and this project
hasn't found enough of those outside the capital-blocked options line.
Concentrating capital in the single best-trusted edge (currently
`turtlesoup`/`AXISBANK.NS` or the `threebar` survivors at 1% risk) beats
spreading it across several correlated, individually-thinner ones.

## Thirty-third: testing a different MARKET STRUCTURE, not another mechanism — this project's two most-tested strategies on genuinely small/mid-cap NSE stocks

Every prior entry ran against large/mega-cap NSE names (the sector sweep's
"39 stocks" and the momentum/low-vol universes were still large/mid-cap
blue chips) — the most analyst-covered, algorithmically-traded, heavily
arbitraged segment of the market. A genuinely different axis from a new
indicator or a new instrument WITHIN the same segment: does the same
already-validated mechanism (not a new signal) do better on a
structurally LESS efficient slice of the market, where less institutional
competition might leave more room for a real technical edge? Reused
`turtlesoup` and `threebar` unmodified (this project's two best-known
survivors — Turtle Soup's smoothest perturbation sweep, 3-bar breakout's
strongest hit rate/sizing response) against a fresh 12-stock genuine
small/mid-cap universe never used elsewhere in this project
(`DEEPAKNTR.NS`, `CROMPTON.NS`, `RADICO.NS`, `APLAPOLLO.NS`,
`JUBLPHARMA.NS`, `SYMPHONY.NS`, `VGUARD.NS`, `RATNAMANI.NS`,
`PERSISTENT.NS`, `GRAPHITE.NS`, `ELGIEQUIP.NS`, `KAJARIACER.NS`).

**Turtle Soup: 3/12 passed** (`DEEPAKNTR.NS`, `JUBLPHARMA.NS`,
`ELGIEQUIP.NS`) — 25%, the IDENTICAL hit rate to its original large-cap
screen (the Twentieth entry). Quarter-split on the three is reasonably
stable (no dramatic decay), but magnitudes are thin (hundreds to low
thousands of rupees per quarter on ₹100,000 capital) — no improvement
over the large-cap result in either hit rate or magnitude.

**3-bar breakout: only 2/12 passed** (`SYMPHONY.NS`, `KAJARIACER.NS`) —
16.7%, WORSE than its large-cap screen's 6/12 (50%, the Twelfth entry).
More strikingly, **5 of the 12 small/mid-cap instruments hit the 10%
drawdown breaker outright** (`JUBLPHARMA.NS`, `VGUARD.NS`,
`RATNAMANI.NS`, `PERSISTENT.NS`, `ELGIEQUIP.NS`) — this strategy's fixed
R-multiple target and structural stop are considerably more prone to
getting whipsawed on smaller-cap volatility than on the large-caps it was
originally validated against. Less market efficiency didn't translate
into more edge here; if anything, higher volatility hurt this specific
strategy's risk control more than it helped find exploitable patterns.

**`SYMPHONY.NS` individually is genuinely clean on quarter-split** — all
4 quarters positive with remarkably consistent, similar magnitude each
time (+2,832 / +3,751 / +2,523 / +3,699), better quarter-to-quarter
stability than almost any other single-instrument survivor in this
project. But two things keep it in the same "real but thin" bucket as
everything else: perturbation is only partially smooth (`compression_atr_mult`
0.5/0.75 pass, 0.25/1.0 fail — a soft middle zone, not a clean gradient;
`target_r_multiple` 2.0/2.5/3.0 pass, 1.5 fails — cleaner), and **sizing
does NOT help the way it did for the original large-cap 3-bar breakout
survivors** — raising risk-per-trade from 0.5% to 1% immediately
drawdown-halts the run (trade count collapsing from 180 to 102) instead
of scaling return up, the "dilutes rather than compounds" pattern RSI-2
and volume's `GC=F` showed, not the "sizing genuinely helps" pattern the
large-cap `threebar` survivors had. Net magnitude at the only safe
setting: **+12,413.60 over 10 years ≈ 1.18%/year** — thin.

**Net verdict**: thirty-third entry, and a clean answer to a real
question this project hadn't asked before (does market segment, not
mechanism, explain the ceiling on everything found so far) — no. Turtle
Soup reproduces its exact chance-level hit rate unchanged; 3-bar breakout
gets meaningfully WORSE (lower hit rate, more drawdown-halts, no sizing
lever). One individually clean survivor (`SYMPHONY.NS`) emerged, but with
weaker perturbation robustness and no sizing response compared to this
project's best large-cap results, landing in the same thin bucket rather
than a breakthrough. This closes off "try a less-efficient market
segment" as a productive direction for this project's two most-validated
mechanisms specifically — the ceiling these strategies hit doesn't appear
to be about which NSE stocks they're pointed at.

## Thirty-fourth: a market-regime classifier — neither regime-filtering nor regime-switching beats what this project already found, but the best-looking filter result exposes a real single-point-fit trap

The first entry to condition on MARKET REGIME rather than test a new
signal, instrument, or portfolio construction. Every prior entry either
ran a strategy unconditionally across its whole backtest window or
combined several strategies simultaneously (Thirty-second) — nothing yet
had asked "does this strategy's edge depend on the prevailing trend/
volatility regime, and can regime awareness be turned into an
improvement?"

Built `regime.py`: a `RegimeClassifier` crossing TREND (today's close vs
a trailing `trend_period`-day SMA — the identical convention
`ConnorsRSI2Strategy`'s own trend filter already uses and this project has
already validated) with VOLATILITY (today's realized-vol reading, ranked
within its own trailing `vol_lookback`-day window — high/low by a median
split), giving 4 buckets (`up_high_vol`, `up_low_vol`, `down_high_vol`,
`down_low_vol`). No lookahead: `classify(close)` is called before
`push(bar)` for that same day, exactly mirroring every `daily_strategy.py`
strategy's own `check_entry(close)` convention. Deliberately reused only
indicators already in `indicators.py` (`sma`, `stdev`) rather than adding
ADX, an HMM regime-switcher, or any new dependency — this project has
consistently favored boring, explainable indicators. Also added
`RegimeGatedStrategy`, a thin wrapper that gates any existing strategy's
`check_entry()` to a set of allowed regimes without modifying the wrapped
strategy at all — a generalization of the Twenty-eighth entry's
Bollinger-specific trend-drift gate into something reusable on any
strategy in this project. 8 new unit tests; full suite 147/147 green.

**Current market regime, checked directly rather than assumed**: `^NSEI`
as of 2026-09-17 is **`down_low_vol`** — below its 200-day SMA, but with
LOW realized volatility. Read as a quiet drift/consolidation below trend,
not a panic decline.

**Hypothesis (a) — regime FILTER**: a specific, motivated pairing, not a
blind sweep — mean-reversion/counter-trend strategies (`rsi2`,
`turtlesoup`) gated to LOW-vol regimes only (calm, range-bound conditions
should favor reversion); continuation/breakout strategies (`threebar`,
`macd`) gated to HIGH-vol regimes only (a compression breakout or a
crossover should need volatility expansion to follow through). Applied to
this project's own 5 best-known survivor pairs (`probe_portfolio_combo.py`'s
`COMPONENTS`, reused directly rather than picking new instruments):
`rsi2`/`RELIANCE.NS`, `threebar`/`SBIN.NS`, `threebar`/`HDFCBANK.NS`,
`macd`/`TCS.NS`, `turtlesoup`/`AXISBANK.NS`.

**Result: no consistent improvement, and the two "wins" are risk
reduction, not return improvement**. `rsi2`/`RELIANCE.NS`: filtering
halved both trades and P&L in lockstep (₹45.4 vs ₹45.3 per trade before/
after) — no edge concentration in low-vol, just fewer trades.
`macd`/`TCS.NS`: actively hurt — baseline +18,739 (2.6% DD) filtered to
-5,527 (10.1% DD), and the filtered version's walk-forward is negative on
BOTH halves. `threebar`/`HDFCBANK.NS`: also hurt (₹18.5/trade baseline
down to ₹3.2/trade filtered, drawdown unchanged). Two looked like wins on
drawdown alone — `threebar`/`SBIN.NS` (10.5%→0.2% DD at an unchanged
₹178/trade) and `turtlesoup`/`AXISBANK.NS` (1.1%→0.2% DD, though per-trade
edge dropped) — but even these are lower TOTAL return (fewer trades), not
a better edge.

**The best-looking case doesn't survive perturbing the regime thresholds
themselves — the real finding of this entry**: per this project's own
methodology, a regime boundary is itself a parameter and must be
perturbed like any other. Swept `vol_period` (10/20/30/40) x
`vol_lookback` (126/252/378) on `threebar`/`SBIN.NS`'s filtered result:
**only `vol_period=20` — the exact default — avoids a drawdown-halt at
every tested `vol_lookback`**; every other `vol_period` value
drawdown-halts in 2 of 3 lookback settings and two cells flip net negative
(`vol_period=30, vol_lookback=378`: -4,842; `vol_period=40,
vol_lookback=252`: -975). This is the identical "peaks suspiciously close
to the exact default" single-point-fit signature this project has already
learned to distrust (Squeeze's `length` sweep, `AXISBANK.NS`'s SuperTrend
`st_period` sweep) — the apparent drawdown improvement was curve-fit noise
around one specific threshold value, not a real regime effect. (Quarter-
split on the unperturbed default was itself mixed, for what it's worth: 2
of 4 quarters negative, though Q4 2024-2026 — the most recent — was the
*strongest*, no decay red flag; moot given the perturbation failure.)

**Hypothesis (b) — regime SWITCH**: a genuinely different shape from the
Thirty-second entry's simultaneous fixed-weight blend (found to hurt
because its components were too correlated) — TEMPORAL allocation
instead. On `AXISBANK.NS` (Turtle Soup's cleanest single survivor, the
Twentieth entry), gated `TurtleSoupStrategy` to choppy/low-vol regimes and
`DonchianBreakoutStrategy` (not previously tested on this stock) to
trending/high-vol regimes, each on its own half-capital slice, merged
chronologically via the same `dated_trades` pattern `probe_portfolio_
combo.py` established.

**Result: ~flat, 0.02%/year — clearly worse than running `turtlesoup`/
`AXISBANK.NS` alone** (1.71%/year at 1.1% drawdown, the Twentieth/Thirty-
second entries' own established number). The trending-gated Donchian leg
was a net loser on its capital slice (₹47,766 final from a ₹50,000
slice), dragging the combined result down to breakeven despite the
choppy-gated Turtle Soup leg finishing positive (₹52,410). Same
conclusion as the Thirty-second entry reached by a different route:
combining this project's components — whether simultaneously (fixed
weights) or temporally (regime-gated) — has twice now underperformed
concentrating capital in the single best-trusted edge.

**Net verdict**: thirty-fourth entry, and both regime hypotheses tested
here are cleanly rejected, not just weakly disconfirmed — the filter
hypothesis's one promising-looking case turned out to be exactly the kind
of single-point parameter artifact this project's own perturbation
discipline exists to catch, and the switching hypothesis reproduces the
Thirty-second entry's "combining doesn't help" conclusion via an entirely
different construction. Useful reusable infrastructure regardless of this
particular null result: `regime.py`'s `RegimeClassifier`/`current_regime()`
gives this project a real, no-lookahead answer to "what's the market doing
right now" (currently: `down_low_vol` on `^NSEI`) for the first time, and
`RegimeGatedStrategy` is a general-purpose gate any future strategy
candidate can reuse — just remember, per this entry, to perturb the
regime thresholds themselves with the same suspicion applied to every
other parameter before trusting a gate's result. **No mechanism has yet
cleared the bar to actually trade.**

## Thirty-fifth: 52-week-high proximity momentum (George & Hwang) — the best hit rate since 3-bar breakout, two genuinely clean survivors, still thin at safe sizing

A real, decades-documented academic anomaly not yet tried here: George &
Hwang, "The 52-Week High and Momentum Investing" (Journal of Finance,
2004) — stocks trading NEAR their trailing 52-week high tend to keep
outperforming. Genuinely different mechanism from everything else in this
file, including this project's own channel-breakout strategy
(`DonchianBreakoutStrategy`): Donchian trades a FRESH N-day extreme (today
IS a new high); this is a continuous PROXIMITY signal (today is merely
CLOSE to a recent high, needs no new extreme at all) — a much higher
percentage of trading days qualify, by construction.

Implemented as `FiftyTwoWeekHighStrategy` in `daily_strategy.py`
(`--strategy high52w`), reusing `indicators.highest()`/`lowest()` directly
(already present for Squeeze's/Turtle Soup's own channel calculations, no
new indicator needed). Rule: `nearness = close / highest(closes,
lookback_period)`; long when `nearness >= entry_threshold` (default 0.95,
252-day lookback — the paper's own ~52-week window). Symmetric short side
added (this project's own extension, not itself literature-backed, the
same caveat already applied to IBS's/volume's short sides): short when
`close / lowest(closes, lookback_period) <= 1/entry_threshold`. No natural
structural stop (a proximity ratio implies none), so `stop_atr_multiple x
ATR` (default 3.0), matching RSI-2/Squeeze/volume/MACD/Bollinger's
convention. Exit when nearness fades back below `exit_threshold` (default
0.85) or `max_hold_days` (60) times out. No-lookahead: highest/lowest
computed from `self._closes` through yesterday, compared against `close`
(today) — identical convention to Donchian/ThreeBarBreakout. 7 new unit
tests; full suite 154/154 green.

**Screening result: 4/10 tradable instruments passed** (both walk-forward
halves positive, no drawdown-halt; ~5bps-equivalent ₹20/round-trip
commission) — `^NSEI`, `TCS.NS`, `AXISBANK.NS`, `GC=F`, on the same
12-instrument set used throughout this file
(`^NSEI`/`^NSEBANK`/`RELIANCE.NS`/`TCS.NS`/`INFY.NS`/`HDFCBANK.NS`/
`ITC.NS`/`SBIN.NS`/`AXISBANK.NS`/`WIPRO.NS`/`CL=F`/`GC=F`).
**`^NSEBANK` produced zero trades and is excluded from the hit-rate
denominator, not counted as a pass** — diagnosed directly, not assumed: at
Bank Nifty's price level (tens of thousands of points) and the default
3xATR stop, `risk.position_size()`'s `risk_amount / per_unit_risk`
rounds down to 0 whole units at the standard 0.5% risk-per-trade on
₹100,000 capital. This is the identical index-sizing artifact the
Thirty-second entry already found and worked around (swapped `^NSEI` for
`RELIANCE.NS` in the portfolio-combo test) — not a bug in this entry's
code, confirmed by checking the strategy's own `check_entry()` in
isolation (1,194 raw signals over 10 years, well above zero) before
looking at the sizing layer. **40% (4/10) is meaningfully above the
chance-level band this project has repeatedly used as a disqualifying
signal** (sector sweep 18%, Squeeze 25%, Turtle Soup 25%, volume 30%,
SuperTrend 33%, PEAD's real 3/20 15%) — the best hit rate since 3-bar
breakout's 50% (Twelfth entry).

**Quarter-split separates the four passers exactly the way this file's
own screening standard exists to catch**:
- **`AXISBANK.NS` fails outright** — 3 of 4 quarters negative
  (-556/-849/-371, only Q3 positive) despite passing the 2-way
  walk-forward screen cleanly. The exact "hollow consistency" pattern
  this file has flagged before (Donchian's oil case, MACD's `AXISBANK.NS`
  halt): a coarser 2-way split can miss decay a finer 4-way split catches.
  Dropped from further consideration.
- **`^NSEI` shows the now-familiar recent-quarter-decay signature** —
  Q1-Q3 positive (+4/+3,667/+2,652), **Q4 (2024-2026, the most recent and
  most relevant window) negative** (-1,515). Flagged, not disqualifying on
  its own (a single-instrument mean-reversion-adjacent signal, not a
  broad basket bet — see the Cross-mechanism synthesis entry), but
  weaker evidence than the two clean survivors below.
- **`TCS.NS` is genuinely clean** — all 4 quarters positive (+4,820 /
  +1,477 / +790 / +1,162), decelerating but never negative, no decay red
  flag at all.
- **`GC=F` (gold) fits this project's established Q4-favorable pattern**
  for single-instrument mechanisms — 2 of 4 quarters negative (-132,
  -890) but **Q4 is the strongest by far** (+4,795), the same
  "most-recent-quarter-is-best" shape already seen in IBS's/volume's gold
  survivors and options-selling, not the "historic run now flat" pattern
  that disqualified the broad basket bets.

**Perturbation on the two survivors that matter (`TCS.NS`, `GC=F`), cross-
checked against `^NSEI` for context, is the smoothest sweep in this
project since 3-bar breakout**: `entry_threshold` (0.90/0.93/0.95/0.97)
and `lookback_period` (126/189/252/378) are BOTH positive on BOTH
walk-forward halves at every single tested value for `TCS.NS` and `GC=F`
— 15 of 16 cells clean, the one exception being `TCS.NS` at
`lookback_period=126` (out-of-sample flips to -1,557) which is itself
informative: 126 days is only half the paper's own window, a real
deviation from the tested mechanism, not a nearby nudge. `exit_threshold`
(0.75/0.80/0.85/0.90) is flat/monotonic and all-positive on both
instruments with no cliffs anywhere. This is real Davey-style robustness,
not a single-point-fit artifact like the regime classifier's `threebar`
result in the prior entry.

**Sizing helps up to a point, the same "1% is near the safe ceiling"
pattern as Turtle Soup/SuperTrend/MACD/Squeeze**: `TCS.NS` 0.5% risk gives
+5,802/10y (0.1% DD, ≈0.58%/yr); 1% gives **+13,102 (0.3% DD, ≈1.31%/yr)**
— genuine roughly-linear scaling; 2% drawdown-halts (11.3% DD). `GC=F`
0.5% gives +9,003 (0.5% DD, ≈0.90%/yr); 1% gives **+19,931 (0.9% DD,
≈1.99%/yr)**; 2% also halts (10.3% DD).

**Net verdict**: thirty-fifth mechanism, and by initial hit rate (40%,
second only to 3-bar breakout's 50%) and perturbation smoothness (tied
for the cleanest in this project) one of the strongest screening results
found here — but the magnitude at safe sizing (1.3-2.0%/year) lands in
the same "real but thin" bucket as Turtle Soup/SuperTrend/MACD, not a
breakthrough past what this project has already found. Two genuinely
clean survivors (`TCS.NS`, `GC=F`) rather than the usual lone-instrument
flag makes this comparatively well-corroborated evidence within this
project's own standard, but per the Eighteenth/Thirty-first entries'
established practice, 2 clean + 1 flagged out of 10 tested still isn't
"found" — it's the strongest candidate for a future retest-on-more-
instruments pass (the same next step that confirmed SuperTrend's `CL=F`
as a lucky draw and would need to be run here before trusting this
further). **No mechanism has yet cleared the bar to actually trade.**

## Thirty-sixth: 52-week-high widen-and-check — the exact retest the Thirty-fifth entry called for; true clean-survivor rate collapses to chance-level on a 40-stock universe

The Thirty-fifth entry's own verdict named its next required step: retest
`high52w` on more instruments before trusting a 2-clean-out-of-10 screen,
the same discipline that caught PEAD's Thirtieth/Thirty-first entries
(clean at 20 stocks, fell apart at 40) and confirmed SuperTrend's `CL=F`
as a lucky draw. Reused `probe_pead.py`'s existing 40-stock NSE large-cap
`UNIVERSE` directly (no reason to hand-roll a second one) and
`backtest_daily.py`'s existing `simulate_daily`/`walk_forward_daily`/
`STRATEGIES["high52w"]` unchanged, via a new thin driver
(`probe_high52w_widen.py` — no new strategy logic, so no new unit tests,
consistent with every other `probe_*.py` in this project). Same ₹20/
round-trip commission and default params (`entry_threshold=0.95`,
`lookback_period=252`) as the original screen.

**Walk-forward alone looks like clean replication**: 15/40 (37.5%) passed
— close to the original 10-stock screen's 40% hit rate, not the immediate
reshuffle PEAD showed. `TCS.NS` passes again (in-sample +5,026/
out-of-sample +1,438, both clearly positive).

**Quarter-split is where it actually falls apart.** Running all 4 quarters
on all 15 walk-forward passers (the same finer check that caught
`AXISBANK.NS`'s hollow consistency in the original entry) leaves only
**2 of 15 (13%) with all 4 quarters positive**: `TCS.NS` (+4,820/+1,477/
+451/+1,391 — reproduces the original entry's own finding almost exactly,
small numeric drift only from the 10y data window rolling forward by a
few days between runs) and a new name, `NESTLEIND.NS` (+2,181/+1,081/
+2,121/+133). Every other walk-forward passer — including several with
strong-looking in-sample/out-of-sample numbers, e.g. `HCLTECH.NS`
(+3,850/+710) or `BPCL.NS` (+175/+2,225) — has at least one negative
quarter once split 4 ways: `AXISBANK.NS` reproduces its own original
hollow-consistency numbers almost exactly (-556/-849/+1,631/-371, only
Q3 positive), confirming that finding wasn't a fluke either.

**2 clean survivors out of the full 40-stock universe is 5%** — at or
below every chance-level band this project has used to disqualify a
result (PEAD's real 3/20 was 15%, sector sweep 18%, Squeeze/Turtle Soup
25%, volume 30%, SuperTrend 33%). The Thirty-fifth entry's headline 40%
hit rate was a 2-way-split artifact: it measured how many instruments
pass a coarse screen, not how many have a real, decay-resistant edge —
the same distinction the quarter-split step exists to draw everywhere
else in this file. `TCS.NS`'s numbers reproducing almost exactly across
two independent runs rules out a code/data-fetch bug, but stability of
one instrument's backtest is not evidence the underlying edge is real —
it just means the measurement is reliable, same caveat already applied to
SuperTrend's `CL=F` before its own retest closed it.

**Net verdict**: this does NOT strengthen the Thirty-fifth entry's
finding — it reveals the original screen overstated it. Closing
`high52w` the same way PEAD and SuperTrend's oil survivor were closed:
`TCS.NS`/`NESTLEIND.NS` are flagged as two individual lucky draws from a
40-name sweep, not a corroborated mechanism. **No mechanism has yet
cleared the bar to actually trade.**

## Thirty-seventh: the overnight-return anomaly — a real gross edge confirmed in the data, but structurally too small to survive any realistic cost, at every threshold tested

A genuinely different mechanism from everything else in this file: the
overnight-return literature (Lou, Polk & Skouras 2019, "A Tug of War:
Overnight versus Intraday Expected Returns"; Cliff, Cooper & Gulen 2019,
"There's No Place Like Home") documents that a large share of a stock's
total return accrues overnight (yesterday's close → today's open), not
intraday, and that the overnight component shows its own short-term
persistence. Nothing in this project has ever decomposed a bar's return
into its overnight vs. intraday pieces before.

Implemented as `OvernightMomentumStrategy` in `daily_strategy.py`
(`--strategy overnight`): `trailing_overnight` = mean of `(open_i /
close_{i-1} - 1)` over the last `lookback` days (default 5), computed
entirely from bars already pushed through **yesterday** — no same-day
open is ever needed, so this strategy stays compatible with the existing
`check_entry(close)` interface unchanged. Long when `trailing_overnight
>= entry_threshold` (default 0.15%); short when `<= -entry_threshold`.
Enters at TODAY's close, holds exactly one overnight leg, exits at
TOMORROW's open — a single-bar hold, not a same-day swing.

This is the first strategy in this file that needed an actual engine
change, not just a new class: `simulate_daily()` always evaluated
`check_exit()` against `bar["close"]` and stop-checked the full
open→high→low→close path. An overnight-only strategy never holds past
the open, so checking high/low/close for a stop would be testing a hold
period the strategy doesn't have. Added one minimal, opt-in flag —
`exit_at_open` on the strategy object (default `False`, so every existing
strategy is byte-for-byte unchanged) — that makes `simulate_daily()`
check only `bar["open"]` for a stop and exit at `bar["open"]` instead of
`bar["close"]`. `OvernightMomentumStrategy.check_exit()` itself is
trivially `return True` — the interface guarantees it's only called while
a position is open, and this strategy never holds past the very next bar,
so there is nothing to check. 8 new unit tests (7 on the strategy class in
isolation, 1 engine-level test proving a catastrophic intraday low on the
exit bar is correctly ignored — only the open matters); full suite
162/162 green.

**Screened on the full 40-stock universe from the start** (`probe_pead.py`'s
`UNIVERSE`, `probe_overnight.py`) — the discipline the Thirty-fifth/
Thirty-sixth entries just learned the hard way, rather than repeating the
small-screen-then-widen cycle a third time. **0/40 passed walk-forward**,
every single instrument net-negative on both halves, and trade counts
were extremely high (~800-1,000 round trips over 10 years per name — the
default 0.15% threshold on a 5-day average is loose enough to trigger on
most days).

**Before closing it as a flat failure like Bollinger Bands (Twenty-seventh
entry, 0/12), checked whether this is "no edge" or "real edge, killed by
cost"** — a distinction this project has drawn before (gold/silver ratio
had no gross edge at all; several single-instrument survivors have real
edges that are merely thin). Re-ran three names
(`TCS.NS`/`RELIANCE.NS`/`ITC.NS`) at **zero commission**: all three came
back clearly **gross-positive** on both walk-forward halves (`TCS.NS`
+2,223/+507; `RELIANCE.NS` +4,784/+2,120; `ITC.NS` +7,604/+2,990) — a
real, reproducible gross edge, not noise.

**Then swept `entry_threshold` from 0.003 to 0.05 with realistic ₹20/
round-trip commission, on all three names, to see if raising the bar and
cutting trade frequency could let the edge outrun the cost.** It doesn't,
anywhere on the curve: losses shrink monotonically as the threshold
rises (`TCS.NS`: -3,285/-4,548 at 0.3% → -754/-1,185 at 0.5% → -153/-379
at 1.0%) but never cross into net-positive before trade count collapses
toward zero (4 trades at 2%, 0 trades at 5%) — the same monotonic,
cliff-free shape this project trusts for perturbation smoothness, just
smoothly converging to zero rather than to a profit. Back-of-envelope
confirms why: at the loosest default threshold, `TCS.NS`'s gross edge
averages roughly ₹3-4 per trade — an order of magnitude below the ₹20
round-trip cost already used throughout this file. This is not a
threshold-tuning problem (unlike most of this project's "thin at safe
sizing" survivors); the edge's *magnitude per trade* is intrinsically
too small for retail-equivalent transaction costs, at every point tested.

**Net verdict**: the overnight-return anomaly is real in this dataset —
confirmed with a clean gross/net decomposition, not just asserted — but
uneconomical to trade at any operating point on the threshold curve
tested here. Closed for a different reason than any prior entry: not
decay, not a lone-instrument fluke, not a capital-tier wall, but a gross
edge too small relative to realistic per-trade costs to ever clear zero,
no matter how the entry filter is tuned. **No mechanism has yet cleared
the bar to actually trade.**

## Thirty-eighth: monthly cross-sectional IBS rotation — the strongest result in the project's history, but a random-control check keeps it short of "proven"

The originally-planned mechanism for this entry was classic 12-1 skip-month
cross-sectional momentum (Jegadeesh & Titman) — abandoned before
implementation once a CLAUDE.md check showed the Fourth entry already
tested this project's own 6-1 monthly-rebalanced cross-sectional momentum
variant and it decayed to a Q4 loss; a 12-month lookback is the same
basket-wide-directional shape, not a genuinely different mechanism.
Sector-index rotation was considered next and also ruled out on a data
check: only `^CNXIT`/`^CNXPHARMA`/`^NSEBANK` return more than one day of
history via yfinance, too few sectors to rotate across.

What got built instead: the Thirteenth entry's Internal Bar Strength
signal (`(close-low)/(high-low)`, real but thin as a single-instrument
daily trigger — one gold survivor at a 12.5% hit rate), turned into a
**monthly cross-sectional rank** across the 40-stock `probe_pead.py`
`UNIVERSE`: at each month-end, rank all 40 names by their trailing 5-day
average IBS, go long the 5 most-oversold (lowest IBS), equal-weighted,
hold one month, rebalance. `probe_ibs_rotation.py`. Reuses momentum
rotation's zero-delivery-brokerage equity cost model (0.2% STT+stamp both
legs, ~₹16 DP charge on the sell leg). No new dependency; `internal_bar_strength`
already lived in `indicators.py`.

**A real bug caught mid-investigation, worth recording**: the first
version picked the trading-day calendar as `max(series.values(), key=len)`
— "whichever of the 40 stocks has the most bars." Several stocks tie on
bar count, so this silently tie-broke on dict iteration order, and a
transient yfinance failure on any one name (several "possibly delisted"
warnings appeared mid-session on names that plainly aren't, e.g.
`SUNPHARMA.NS`/`TATASTEEL.NS` — an intermittent fetch problem under rapid
repeat calls, not a data-provider fact) could silently swap which stock's
calendar got used, shifting month-end dates and changing results
run-to-run — caught because a walk-forward run and a quarter-split run on
the *same default parameters* disagreed with each other. Fixed at the
root: `fetch_calendar()` now always pulls `^NSEI` specifically as the
trading-day source (confirmed reliable 10y history earlier in this
project), and `build_price_series()` retries a failed per-stock fetch up
to 3 times. Reproducibility verified afterward (two `simulate()` calls on
the same in-memory series, same params, return byte-identical results).

**Default (`top_k=5, lookback=5`) screening**: full 10y period, 15.51%/yr
at 26.6% max drawdown, 63% of months net-positive. Walk-forward: both
halves positive (24.41%/yr in-sample, 6.65%/yr out-of-sample) —
decelerating but consistent. **Quarter-split (the check that killed the
Thirty-fifth/Thirty-sixth and Fourth entries): all 4 quarters positive**
(Q1 19.23%, Q2 29.47%, Q3 8.37%, Q4 4.79%/yr) — Q4 is the weakest quarter
here, a real deceleration worth flagging honestly, but still positive,
unlike momentum rotation's Q4 (-7.1%) or PEAD/52-week-high's collapse.
**Re-ran quarter-split on 4 more parameter combinations** (`top_k=3,lb=10`;
`top_k=7,lb=1`; `top_k=10,lb=5`; `top_k=3,lb=1`) — **every single one had
all 4 quarters positive**, Q4 ranging 1.30% to 21.45%/yr depending on
config. **Perturbation**: 16/16 `top_k`×`lookback` cells (3/5/7/10 ×
1/3/5/10 days) positive on both walk-forward halves — the smoothest sweep
in this project's history, no cliffs.

**Attribution check (this project's PEAD lesson — a "broad" result can
still be 2-3 names in disguise)**: all 40 universe stocks got selected at
least once across 120 months; **30/40 (75%) were individually net-positive
contributors**; the top-3 contributors' combined share of total net P&L
was only 31.4%. Far broader than any prior "survivor" in this project
(PEAD's 3/20, IBS's 1/8, SuperTrend's 1/9) — this is not a lone-name or
small-cluster fluke.

**Beats its benchmarks on both return and drawdown**: `^NSEI` buy-and-hold
over the identical window returned 10.21%/yr at 38.4% max drawdown; an
equal-weight, monthly-rebalanced buy-and-hold of the full 40-stock
universe (isolating whether the edge is really "pick oversold names" vs.
just "hold this particular universe") returned only 5.86%/yr at 37.1%
drawdown — *worse* than the index, meaning the universe itself carries no
free lunch and the IBS ranking is doing real work on top of it.

**The check no prior entry in this project has run, and the reason this
isn't declared a clean win**: is 15.51%/yr distinguishable from luck, or
is picking *any* 5 stocks a month from this universe roughly this good
in a rising market? Simulated 200 seeds of a random-5-stocks-per-month
control (same rebalance mechanics, same costs, same eligible universe):
mean final capital ₹311,901 (std ₹103,252) vs. the actual IBS strategy's
₹420,847 — **86.5th percentile, z ≈ 1.06**. Directionally real and
persistent (return AND drawdown both beat the random-control median;
drawdown alone beats 86% of random draws), but a z-score of ~1.06 is not
classically significant (one draw among the 200 random seeds — ₹464,802 —
actually beat the real strategy outright). The consistency across 5
parameter configs' quarter-splits and the 16/16 perturbation sweep are
more persuasive on their own than this single distributional check, but
none of that changes what the random-control test itself shows: real, but
not an overwhelming statistical outlier.

**Unresolved, flagged not fixed (same caveat the Fourth entry raised and
never closed)**: `UNIVERSE` is hand-picked using *today's* well-known
large/mid-caps, not a point-in-time historical constituents list — a real
survivorship-bias risk with no free fix available (same unresolved gap as
momentum rotation's).

**Net verdict**: the best-looking result in this project's 38-entry
history by every check applied — cleanest quarter-split (positive at
every quarter, across 5 different parameter configs), smoothest
perturbation sweep, broadest attribution (75% of names contribute
positively, no concentration), and it beats both the index and an
equal-weight-universe control on return and drawdown. Still short of
"proven": the random-control z-score is modest (~1.06), survivorship bias
in the universe selection is real and unresolved, and this project's own
standard (see the framing after PEAD/52-week-high) is that a real,
repeated pattern is a stronger foundation than any single backtest number
— which is exactly what quarter-split-across-5-configs and the
perturbation sweep provide here, more thoroughly than any prior entry.
**Not ported into `daily_strategy.py`'s tested single-instrument
architecture** — this is a portfolio-level, cross-sectional strategy with
a genuinely different execution shape (monthly full reallocation across
multiple concurrent names, not a single-instrument entry/exit), so it
stays a probe script, same treatment as momentum rotation and PEAD.
**Recommended next step, not yet done**: before this is anywhere close to
a "trade real money" decision, paper-track this specific rule (top_k=5,
lookback=5, monthly rebalance) forward in real time — every prior "best
result yet" in this project (options selling, PEAD, 52-week-high) fell
apart on a check run AFTER it looked good, and the one check this entry
cannot run is genuinely out-of-sample data that didn't exist when the
backtest was written. **No mechanism has yet cleared the bar to actually
trade** — this is the closest any mechanism in this project has come, not
an exception to that standing verdict.

## Thirty-ninth: stress-testing the IBS rotation finding — it survives widening, for the first time in this project's history

Direct follow-up on the Thirty-eighth entry's own open questions, treated
with the same discipline PEAD's Thirtieth/Thirty-first entries and
52-week-high's Thirty-fifth/Thirty-sixth entries established: a
promising cross-sectional result is not trusted until it's been widened,
re-tested for significance more rigorously, and sanity-checked for real
capital feasibility. Three checks, in order:

**1. Widen the universe.** `probe_ibs_rotation_widen.py` combines the
40-stock `probe_pead.py` `UNIVERSE` with the Thirty-third entry's 12-stock
small/mid-cap universe (never combined before this entry) into a 52-stock
set — a genuinely different, larger pool, not a resample of the same
names. Re-ran the default config (`top_k=5, lookback=5`) plus 5 more
parameter combinations (`top_k=3,lb=10`; `top_k=7,lb=1`; `top_k=10,lb=5`;
`top_k=8,lb=5`; `top_k=3,lb=5`) with the exact same walk-forward +
quarter-split checks used at 40 stocks.

**Result: the finding gets STRONGER, not weaker, on the wider universe —
the first time any promising cross-sectional result in this project's
history has done that rather than collapsing.** Default config: full
period 22.11%/yr at 38.2% max drawdown (up from 40-stock's 15.51%/yr),
walk-forward both halves positive and consistent, **all 4 quarters
positive** (Q1 24.97%, Q2 23.84%, Q3 17.87%, Q4 20.02%/yr — Q4 no longer
even the weakest quarter, unlike the 40-stock run). **All 6 parameter
configs tested have all 4 quarters positive** — the same "every config
survives quarter-split" pattern the Thirty-eighth entry found at 40
stocks, now confirmed at 52. Attribution: **52/52 stocks selected at
least once, 38/52 (73%) individually net-positive** (vs. 40-stock's 75%
— essentially unchanged breadth), and **top-3 concentration actually
IMPROVED to 26.5%** (vs. 40-stock's 31.4%) — adding 12 new names diluted
concentration further rather than revealing the edge was secretly
concentrated in the original universe. The best individual contributor
(`ELGIEQUIP.NS`, +73,954) is one of the newly-added small/mid-cap names,
not a large-cap holdover — a positive sign the edge isn't an artifact
specific to the original 40-stock selection.

**2. Strengthen the significance test.** `probe_ibs_rotation_significance.py`
reruns the Thirty-eighth entry's random-control check with 1,500 seeds
(vs. the original 200) on the widened 52-stock universe, at three nearby
portfolio sizes (`top_k=3, 5, 8`) rather than just the default 5, to check
the statistical edge isn't itself a lucky parameter pick:

| top_k | actual final capital | random mean (std) | percentile | empirical p | z |
|---|---|---|---|---|---|
| 3 | ₹771,423 | ₹360,162 (±192,839) | 96.3 | 0.0367 | 2.13 |
| 5 | ₹731,663 | ₹358,469 (±135,626) | 98.5 | 0.0153 | 2.75 |
| 8 | ₹619,936 | ₹346,709 (±99,995) | 98.6 | 0.0140 | 2.73 |

**All three clear conventional significance** (p < 0.05; `top_k=5` and
`top_k=8` clear p < 0.02) — a real change from the Thirty-eighth entry's
own z≈1.06/p≈0.135 finding on the narrower universe. Both effects
plausibly contribute: a bigger, more diversified stock pool shrinks the
random-control's own variance (more names to draw from smooths out lucky/
unlucky single-stock draws), and the actual strategy's edge itself grew
on the wider universe (per check 1 above) — either alone would move the
z-score up; here both moved the same direction together. This is now a
properly powered test (1,500 draws, not 200) rather than a
directionally-suggestive one.

**3. Cost and capital sanity check.** Confirmed `probe_ibs_rotation.py`'s
`simulate()` already includes this project's standard realistic equity
cost model throughout (0.2% STT+stamp both legs + ~₹16 DP charge on the
sell leg, the same zero-delivery-brokerage model momentum rotation and
low-volatility established) — no shortcut in the reported numbers, no
fix needed. Capital feasibility is genuinely mixed, not a clean pass: at
the ₹100,000 capital this project's headline numbers use, `top_k=5` means
₹20,000 notional per position, comfortably covering even the priciest
name in the widened universe (`MARUTI.NS`, ~₹12,370/share). But at
**₹30,000** — the LOW end of this project's own stated ₹30,000-100,000
target range (README's "Ninth" reference) — `top_k=5` notional drops to
only ₹6,000/position, which can't buy even ONE whole share of `MARUTI.NS`
or several other names above ₹6,000 (`BAJAJ-AUTO.NS` ~₹11,475,
`ULTRACEMCO.NS` ~₹10,808, `DIVISLAB.NS` ~₹9,307, `EICHERMOT.NS`
~₹7,530) if any of them gets picked as a most-oversold name that month.
This is NOT the same severity as the commodity/FX fixed-lot-size wall
(Twenty-fifth/Twenty-sixth entries) that fully blocked those strategies —
equities allow any whole-share quantity, so the fix is simply rounding
down to whole shares (occasionally skipping an expensive pick or running
with fewer than `top_k` names some months) rather than a hard capital
floor — but it's a real, previously-unmodeled gap between this project's
continuous-notional simulation and what a genuine ₹30,000 account could
actually execute, and it should be fixed with real share-quantity
rounding before paper-trading at the low end of this project's target
capital range specifically. Not an issue at ₹100,000, which is what every
number in this entry and the Thirty-eighth entry above uses.

**Net verdict**: this is the first cross-sectional finding in this
project's history to survive a genuine universe-widening test — a
meaningfully different outcome from PEAD (fell apart), 52-week-high (fell
apart), and every other "looked great, then widened" result here. Still
not declared "ready to trade," consistent with this project's own
standing bar (see the Thirty-eighth entry's own framing): survivorship
bias in both universes (hand-picked today's well-known names, not a
point-in-time historical constituent list) remains real and unresolved,
and genuinely out-of-sample forward data — the one check no backtest can
run — still hasn't been collected. **Recommended next step, now sharper
than the Thirty-eighth entry's version**: paper-track this exact rule
(top_k=5, lookback=5, monthly rebalance, 52-stock universe) forward in
real time, with real whole-share position sizing fixed first if testing
near the ₹30,000 end of this project's target capital range. **No
mechanism has yet cleared the bar to actually trade** — this remains the
closest any mechanism in this project has come, and is now more
rigorously corroborated than before, not merely repeated.

## Fortieth: survivorship-bias stress test on the IBS rotation finding — the strategy survives real historical blowups added to the universe, but the reason why raises a different concern

Direct follow-up on the Thirty-eighth/Thirty-ninth entries' last open gap:
`UNIVERSE` (`probe_pead.py`) and `SMALL_MID_CAP_UNIVERSE`
(`probe_ibs_rotation_widen.py`) are both hand-picked using *today's*
well-known large/mid-caps. Any stock that delisted, went bankrupt, or
collapsed during the ~10y backtest window is invisible to them — a
long-only equal-weight strategy tested only against companies that
survived and (mostly) thrived is mechanically flattered versus what it
would have actually faced running in real time.

**What a true fix would need, and why it isn't available here**: a
genuine point-in-time historical index-constituents list (which stocks
were actually large/mid-cap on each date over the last 10 years),
typically a paid data product. yfinance has no historical-membership API.
Checked directly whether even the *individual stocks* are recoverable:
`DHFL.NS` and `RELCAPITAL.NS` — two real 2019-2021 NSE large/mid-cap
collapses (Dewan Housing Finance's IBC bankruptcy, Reliance Capital's
resolution) — both return **zero rows** from yfinance. They are fully
invisible to this project's data source; there is no free fix for those
specific cases.

**What partial check was possible**: four other real, equally famous NSE
catastrophic collapses never stopped trading and so are still fully
present in yfinance's history — confirmed via a direct pull before
building anything: `JETAIRWAYS.NS` (grounded 2019, NCLT resolution, -98%
from peak), `YESBANK.NS` (2020 near-collapse/RBI reconstruction, -97%),
`RCOM.NS` (Reliance Communications insolvency, -99%), `PCJEWELLER.NS`
(fraud-driven collapse, -99%). None were in either existing universe —
added all four to the widened 52-stock set (56 total,
`probe_ibs_rotation_survivorship.py`) as a real, non-synthetic worst-case
stress test: does the IBS-oversold ranking mechanically buy into these
falling knives, and how much does that erode the Thirty-ninth entry's
numbers?

**Result: it doesn't erode them — the headline number went UP, not
down.** Default config (`top_k=5, lookback=5`) on the 56-stock stress
universe: 23.95%/yr (vs. the 52-stock universe's 22.11%/yr), walk-forward
consistent across both halves, **all 4 quarters positive** (20.7% /
20.5% / 32.3% / 21.0%/yr). Reproduced across 3 more parameter configs
(`top_k=3,lb=10`; `top_k=7,lb=1`; `top_k=10,lb=5`) — all 4 quarters
positive at every one, same pattern the Thirty-eighth/Thirty-ninth
entries established.

**But the reason why is the actual finding, and it's a different concern
than expected**: at the default config, `PCJEWELLER.NS` was the single
best contributor in the entire 56-stock universe (+232,941, more than any
large-cap name), `RCOM.NS` was 2nd (+91,234), `YESBANK.NS` was 3rd
(+65,476) — three of the four inserted catastrophic collapses became
**top-3 contributors**, pushing top-3 concentration UP to 50.4% (worse
than the Thirty-ninth entry's 26.5% at 52 stocks, and worse even than the
Thirty-eighth entry's original 31.4% at 40). The mechanism: monthly
rebalance means the strategy never holds through a full collapse — it
buys a one-month bounce after a deep drawdown and exits before the next
leg down, repeatedly, and a stock in freefall generates exactly this
setup on a recurring basis. `JETAIRWAYS.NS` was the only inserted
blowup that stayed net-negative, and only modestly (-9,979 to -15,980
depending on config).

This means the finding isn't "survivorship bias doesn't matter here" —
it's **"this strategy's edge is partly a high-volatility-mean-reversion
harvest, not purely a diversified quality-oversold-stock-pick edge,"**
and that changes the honest risk read going forward: a stock in freefall
*during the exact month it's picked* could just as easily continue
straight down instead of bouncing, and nothing in this backtest window
proves the bounce-after-crash pattern will keep recurring at the same
rate going forward. **Also noted, not fully resolved**: re-running the
identical default config produced a materially different top-3 share
(44.0% on one run, 50.4% on a rerun) — consistent with the Thirty-eighth
entry's own documented intermittent yfinance "possibly delisted" retry
behavior silently changing which bars get fetched run-to-run; the
headline all-4-quarters-positive result held in both runs, but the exact
concentration number should be read as noisy at the single-run level, not
a precise figure.

**Net verdict**: the strategy passed the survivorship-bias stress test on
its headline numbers (return, drawdown-consistency, quarter-split) — the
first result in this project's history to survive this specific check —
but the mechanism behind that pass reveals a real, previously-unflagged
concentration risk in catastrophic-collapse bounces that a point-in-time
survivorship fix alone would not have caught. Neither DHFL nor
RELCAPITAL-style *fully-erased* names could be tested at all (no data
exists for them anywhere in this project's stack), so this remains a
partial, not complete, survivorship check. **No mechanism has yet cleared
the bar to actually trade.** The Thirty-ninth entry's recommended next
step (paper-track top_k=5/lookback=5/monthly/52-stock forward in real
time) still stands, now with the added caveat that a real
freefall-in-progress name in the eligible universe should be watched
carefully rather than assumed to bounce.

## Forty-first: forward paper-tracking infrastructure — the IBS rotation finding now has a real, untouched out-of-sample record started

Every entry since the Thirty-eighth has recommended the same next step and
none has done it: this project's best-yet finding needs genuine forward
data, which no backtest can fabricate. This entry builds that
infrastructure rather than testing another mechanism.

`paper_track_ibs_rotation.py` implements the Thirty-ninth entry's
recommended live rule exactly: `top_k=5, lookback=5`, monthly rebalance,
the Thirty-ninth entry's 52-stock `WIDE_UNIVERSE`
(`probe_ibs_rotation_widen.py`'s large-cap + small/mid-cap combination —
NOT the Fortieth entry's 56-stock survivorship-stress set, which was a
one-off worst-case test, not the recommended live universe), ₹100,000
capital, this project's standard equity cost model (0.2% STT+stamp both
legs, ₹16 DP charge on the sell leg).

**Reused rather than rebuilt**: `run_live.py`/`paper_broker.py`/
`kite_client.py` are built for single-instrument intraday Kite polling —
the wrong shape for a monthly cross-sectional multi-name picker fed from
yfinance daily data, so they weren't reused directly. What WAS reused:
`data_yfinance.fetch_candles` (via `probe_ibs_rotation_widen.py`'s
existing `build_wide_price_series`), and — to structurally prevent the
live picker from ever silently drifting from what Thirty-eight through
Forty actually validated — the ranking logic itself. `probe_ibs_rotation.py`'s
`simulate()` had the scoring/sorting loop inlined; extracted it into a
shared `rank_by_ibs()` (plus a `dates_closes_maps()` helper) that both
`simulate()` and `paper_track_ibs_rotation.py` now call, instead of writing
a second copy of the picking logic and hoping it stays in sync. Verified
the refactor changed no behavior: full suite (162 tests) passed
unchanged before writing anything new.

**How it works**: run once a month (matches the strategy's own rebalance
cadence — this is not a real-time daemon). Each run (1) marks the
PREVIOUS run's open picks to market using today's prices and the same
cost model as `simulate()`, appending a realized-P&L record, then (2)
ranks the universe by trailing 5-day avg IBS and logs today's 5 new
picks as the new open position. `paper_track_ibs_rotation_log.json` is
the append-only forward record — **do not edit, reset, or reinterpret
past entries retroactively; the entire point is a record this project's
own backtests cannot fabricate.**

**New test** (`tests/test_paper_track_ibs_rotation.py`): confirms the live
picker selects the identical symbols `simulate()` would have picked given
the same synthetic price data and date, and that `mark_to_market()`'s P&L
matches `simulate()`'s P&L to the cent for the same single-period trade —
this guards against future edits to either the live picker or the backtest
engine silently diverging. Full suite now 164 tests, all green.

**First real forward record, logged 2026-09-17**: `POWERGRID.NS`
(entry_ibs=0.0431, ₹263.35), `RELIANCE.NS` (0.1738, ₹1244.20),
`HINDUNILVR.NS` (0.2145, ₹1951.90), `MARUTI.NS` (0.2509, ₹12367.00),
`BAJFINANCE.NS` (0.2814, ₹1010.70).

**For whoever (a future session or the user) picks this up next**: run
`python paper_track_ibs_rotation.py` again next month (and every month
after) to mark this month's picks to market and log the next set. After
a few months of real forward records accumulate, compare the forward
win rate/return to what the backtest predicted — that comparison, not
another backtest variant, is what will actually move this project's
"no mechanism has yet cleared the bar to actually trade" verdict. Do
not delete or hand-edit `paper_track_ibs_rotation_log.json`.

## Forty-second: does the cross-sectional-rank RECIPE generalize beyond IBS? Partially — RSI-2 rotation passes backtest checks but is statistically weaker

A generalization test, not a new mechanism: entries 38-41 found IBS's real
edge only showed up once it was reshaped from a single-instrument daily
trigger into a monthly cross-sectional rank across many names. Two
structural reasons that reshaping could matter regardless of which signal
drives it — cutting trade frequency ~20x (directly solving the cost-drag
problem that killed the Thirty-seventh entry's overnight anomaly) and
diversifying single-name risk across a basket (the reason every prior
single-instrument survivor stayed "thin"). If the RECIPE itself is what
works, not something specific to IBS, this project has probably been
under-testing several already-real single-instrument signals by running
them the old way (single-instrument, daily).

Tested the obvious candidate: Connors RSI(2) (Third entry, this project's
own most-cited single-instrument survivor), turned into the identical
monthly cross-sectional rank shape as IBS rotation — rank the Thirty-ninth
entry's 52-stock `WIDE_UNIVERSE` by trailing RSI(2) each month-end
(`indicators.rsi(closes, period=2)`, the exact formula
`ConnorsRSI2Strategy.check_entry()` already uses), go long the `top_k`
most-oversold (lowest RSI(2)), equal-weight, monthly rebalance.
Implemented as `probe_rsi2_rotation.py`, reusing
`probe_ibs_rotation_widen.py`'s universe/price-fetch/calendar machinery and
`probe_ibs_rotation_significance.py`'s random-control significance test
unchanged — only the ranking function is new code, no new probe-script
test per this project's established convention (probe scripts don't get
one; `probe_ibs_rotation.py` itself doesn't either).

**Walk-forward and quarter-split both pass, at all three tested portfolio
sizes** — the same headline shape as IBS rotation: `top_k=3/5/8` are all
walk-forward-consistent (both halves positive) and **all 4 quarters
positive** at every size (Q4 2024-2026, the most recent window, positive
in all three: 11.90%/5.91%/12.69%/yr respectively — no decay). Full-period
annualized: 17.11%/yr (`top_k=3`), 15.43%/yr (`top_k=5`), 19.29%/yr
(`top_k=8`) — real, comparable in shape to IBS rotation's own numbers,
though noticeably lower in absolute magnitude than IBS rotation's 52-stock
result (22.11%/yr at `top_k=5`).

**The significance test is where the recipe stops generalizing cleanly.**
Reran the exact 1,500-seed random-control methodology from the Thirty-ninth
entry:

| top_k | RSI-2 rotation actual | percentile | empirical p | z | (IBS rotation's own Thirty-ninth-entry p, for comparison) |
|---|---|---|---|---|---|
| 3 | ₹482,225 | 80.2 | 0.198 | 0.63 | 0.037 |
| 5 | ₹417,665 | 71.5 | 0.285 | 0.43 | 0.015 |
| 8 | ₹579,623 | 97.5 | 0.025 | 2.32 | 0.014 |

Only `top_k=8` clears conventional significance (p<0.05); `top_k=3` and
`top_k=5` — the two sizes IBS rotation cleared most convincingly — do
**not**. This is a materially weaker statistical result than IBS rotation
at the same universe, same seed count, same methodology, same portfolio
sizes.

**Net verdict**: the cross-sectional-monthly-rank recipe generalizes
*partially*, not fully — it reliably produces a backtest-passing shape
(walk-forward consistency, all-quarters-positive, no decay) on a second,
genuinely different signal, which is itself informative: the shape isn't
an IBS-only artifact. But it does NOT reproduce IBS's stronger, more
consistent statistical significance against a random-portfolio control —
IBS's specific same-day positional mean-reversion character appears to
carry more real information than RSI(2)'s 2-day momentum-oscillator
character does, once diversified into a monthly cross-sectional rank.
Read together with the Twenty-second entry's finding that combining IBS
and RSI-2 as an AND-confirmed entry made both worse: these are two
genuinely different signals whose strengths don't simply transfer between
constructions. **Not pursued further as a standalone strategy** — IBS
rotation remains this project's strongest, most rigorously corroborated
finding, and RSI-2 rotation's weaker significance at its own most natural
portfolio sizes doesn't clear the bar this project already set with IBS.
`probe_rsi2_rotation.py` stays a probe script, same treatment as every
other unconfirmed cross-sectional variant tested here. **No mechanism has
yet cleared the bar to actually trade** — IBS rotation (Thirty-eighth
through Forty-first entries) remains the closest.

## Forty-third: completing the generalization survey — the cross-sectional-rank recipe fails cleanly on momentum/breakout signals, only mean-reversion signals carry over

Direct completion of the Forty-second entry's question: IBS (Thirteenth)
and RSI-2 (Third) are both mean-reversion signals, and both produced a
real, backtest-passing shape once reshaped into the monthly cross-sectional
rank recipe (though at different significance strength). This entry tests
the other flavor of signal this project has found real-but-thin — 3-bar
compression breakout (Twelfth entry, this project's best single-instrument
hit rate, 50%) and Turtle Soup failed-breakout fade (Twentieth entry, the
smoothest single-survivor perturbation sweep in this project) — to see
whether the recipe is a mean-reversion-specific lever or a genuinely
general one.

Both strategies' own `check_entry()` needs a stock's OWN high/low at the
decision point, not just closes (same shape-mismatch reasoning as every
other probe here), so they're reimplemented as continuous monthly scores
in a new `probe_breakout_rotation.py` rather than reusing
`daily_strategy.py`'s interface — one file for both strategies (`--strategy
threebar|turtlesoup`), consistent with this project's "fewer files" norm.
Scores, both long-only (mirroring IBS/RSI-2 rotation's own long-only
convention): **3-bar breakout** — bar1/bar2 compressed within
`compression_atr_mult`xATR of each other (the source's own filter), bar3
closing above both, scored as -(breakout size)/ATR (more negative =
bigger breakout = ranked first); **Turtle Soup** — yesterday closed below
the prior `channel_period`-day low (excluding yesterday itself, mirroring
`TurtleSoupStrategy`'s own `lowest(self._lows[:-1], ...)`) and today
closed back above it, scored as -(depth broken + recovery size)/ATR. Same
52-stock `WIDE_UNIVERSE`, monthly rebalance, cost model, and walk-forward/
quarter-split checks as every rotation variant since the Thirty-eighth
entry. No new probe-script unit test, matching this project's established
convention (`probe_ibs_rotation.py`/`probe_rsi2_rotation.py` don't have
one either).

**Both fail at the first check — walk-forward, not a later refinement.**
At the default `top_k=5`: `threebar` rotation is **INCONSISTENT**
(in-sample +22.68%/yr, out-of-sample **-3.63%/yr**) with Q3/Q4 both
negative (Q4, the most recent, -4.07%/yr — a real decay signature, not
just noise); `turtlesoup` rotation is also **INCONSISTENT** (in-sample
+19.41%/yr, out-of-sample -0.43%/yr) with Q3 negative. Per this entry's
own instruction to stop at the first real crack rather than running the
full checklist past a clear failure, quarter-split/perturbation/
significance testing were not run at `top_k=5` for either — the walk-forward
inconsistency alone is disqualifying by this project's own standing
screen. Checked `top_k=3` and `top_k=8` as a quick robustness spot-check
before closing (not a full perturbation grid, given the clarity of the
failure): `top_k=8` is INCONSISTENT for both strategies too; `top_k=3` is
technically same-sign both halves for both, but the out-of-sample half is
barely above breakeven (`threebar` ₹102,481 from ₹100,000 over 5 years,
`turtlesoup` ₹100,492) — essentially flat, not the kind of real,
strongly-positive-both-halves result IBS/RSI-2 rotation produced at any
tested size.

**Net verdict**: the cross-sectional-monthly-rank recipe does NOT
generalize to momentum/breakout-flavored signals the way it did (fully for
IBS, partially for RSI-2) to mean-reversion-flavored ones — a clean,
symmetric answer to the Forty-second entry's open strategic question.
Read together with all four signals now tested under this recipe (IBS:
strong pass; RSI-2: backtest-passing but weaker significance; 3-bar
breakout and Turtle Soup: fail walk-forward outright), the likely
mechanism is structural, not incidental: a mean-reversion signal picks
"most oversold now" every month, which is well-defined for every eligible
stock simultaneously (a genuine cross-sectional ranking); a
breakout/continuation signal only fires on the specific subset of stocks
actively breaking out that month, which is a much sparser, more
regime-dependent condition to rank cross-sectionally, and concentrating a
monthly portfolio in "whichever few names happen to be breaking out right
now" behaves more like a chasing a directional basket bet (the shape this
project's own Cross-mechanism synthesis entry already found decays in the
most recent quarter) than like harvesting a stable cross-sectional
mispricing. **This closes the generalization survey**: the recipe is a
real, reusable lever specifically for this project's mean-reversion
signals, not a universal fix for every thin single-instrument survivor.
IBS rotation (Thirty-eighth through Forty-first entries) remains this
project's strongest, most rigorously corroborated finding, and RSI-2
rotation (Forty-second) its weaker second case. `probe_breakout_rotation.py`
stays a probe script, same treatment as every other tested-and-rejected
variant here. **No mechanism has yet cleared the bar to actually trade.**

## Forty-fourth: does a short leg cut the long-only IBS rotation's drawdown? No — it destroys almost the entire return, because IBS's real edge is one-sided

A well-motivated extension of this project's best-yet finding, not a blind
new-mechanism search: the Thirty-ninth entry's long-only IBS rotation
carries a 38.2% max drawdown on the 52-stock widened universe, and a
meaningful chunk of that is plausibly just NIFTY beta — the entire
long-only basket falls together in a market-wide selloff regardless of
how well the IBS ranking is picking within it. Standard long/short
construction: short the `top_k` most-OVERBOUGHT names (highest trailing
IBS) alongside the existing long leg (most-oversold, lowest IBS), same
monthly rebalance, capital split evenly between the two legs.

**Real-world caveat, stated up front because it matters for how to read
every number below**: NSE cash-equity short selling cannot be held
overnight — a real retail account must square off a short position the
SAME DAY. A month-long short, as modeled here, isn't directly executable
in the cash market at all without Securities Lending & Borrowing (SLB, its
own eligibility/approval/cost process — not modeled) or single-stock
futures (which reintroduce this project's own already-closed fixed-lot
capital-tier wall from the Twenty-fifth/Twenty-sixth entries). The short
leg is costed with the same symmetric round-trip cost model as the long
leg for a clean comparison, but no SLB borrow fee or availability
constraint is modeled. Every long/short number in this entry is a
theoretical hedge-shape check, not a directly tradable retail
construction the way this project's long-only numbers are — worth
knowing regardless of how the hedge itself performed, and it performed
badly enough that the caveat ends up moot in practice.

**Result: the hedge works exactly as intended on risk, and that's the
problem.** `probe_ibs_rotation_longshort.py`, same 52-stock `WIDE_UNIVERSE`,
same cost model, `top_k=5, lookback=5`, run in the same session against
the same data fetch as the long-only baseline for a clean side-by-side:

| | Long-only (Thirty-ninth's construction) | Long/short (this entry) |
|---|---|---|
| Full-period annualized | 22.12%/yr | **-0.19%/yr** |
| Max drawdown | 38.2% | 33.5% |
| NIFTY monthly-return correlation | 0.796 | **0.044** |
| Walk-forward | consistent, both halves positive | **INCONSISTENT** (in-sample -4.35%/yr, out-of-sample +4.65%/yr) |

The short leg did hedge out market beta almost completely (correlation to
NIFTY drops from 0.80 to essentially zero) — but drawdown barely moved
(38.2% → 33.5%, nowhere near proportional to how much beta was removed),
and full-period return collapsed from a strong positive to a small
negative. Isolated the short leg's own P&L directly: on a fixed
half-capital notional (uncompounded, for a clean read), the long leg made
+₹106,274 over 10 years while the short leg made **-₹101,639** — almost an
exact offset. The short leg's own win rate was 38.3% (120 months) — the
"most overbought" names kept RISING in 61.7% of months, not reverting.

**Why, and what it means for this project's understanding of the IBS
signal itself**: this isn't a hedging-construction problem (equal-capital
dollar-neutral is the standard, simplest version, and it was implemented
correctly — the correlation number proves the hedge mechanics worked).
It's that IBS's real edge, at least in this cross-sectional monthly
construction, is **one-sided** — "most oversold" genuinely predicts
above-average forward returns (the long leg, already established across
entries 38-41), but "most overbought" does NOT reliably predict
below-average forward returns; if anything the overbought names show mild
continued momentum. This is consistent with, not contradictory to, the
published IBS literature the Thirteenth entry originally cited — the
documented academic edge for IBS has always been specifically the
long/oversold side, and every "symmetric short side" added anywhere in
this project (IBS's original single-instrument version, volume's CMF+OBV,
52-week-high, this rotation's own daily-trigger ancestor) has been
explicitly flagged throughout this file as this project's own extension,
never itself literature-backed. This is the first time that flagged
extension was actually tested at scale and shown to be a real net drag,
not just an unconfirmed assumption.

**Net verdict**: forty-fourth entry, and a clean, decisive rejection of a
well-motivated hypothesis — adding a short leg to hedge the long-only IBS
rotation's market exposure does not produce a better risk-adjusted
strategy; it trades nearly all of the return for a smaller-than-expected
drawdown improvement, because the short side isn't a real mirror-image
signal. **The long-only construction (Thirty-eighth through Forty-first
entries) remains this project's strongest finding, unmodified by this
result** — this entry rules out one specific improvement path rather than
changing that verdict. Worth remembering for any future attempt to
"long/short-ify" one of this project's other real survivors: check
whether the published/established edge is actually symmetric before
assuming a mirror-image short leg will behave the same way the long leg
does, rather than assuming standard long/short construction is a free
risk-reduction lever. **No mechanism has yet cleared the bar to actually
trade.**

## Forty-fifth: a targeted NIFTY-futures beta hedge cuts drawdown cleanly in backtest — and is blocked by the exact same capital-tier wall this project already closed out for every other Nifty derivative

The Forty-fourth entry's per-stock short leg fought IBS's real, one-sided
edge and destroyed almost the entire return trying to hedge market beta.
This entry tests a more targeted construction instead: leave every
individual stock pick completely untouched (100% of the stock-picking
edge intact) and hedge ONLY the portfolio's broad market-beta component,
using a single short NIFTY futures position sized via a real OLS
regression of the long-only strategy's own monthly returns against
NIFTY's monthly returns (`statistics.linear_regression`, not just reusing
the Forty-fourth entry's correlation number — beta and correlation are
different statistics, and beta is what actually determines the right
hedge notional).

NIFTY futures are a genuinely different instrument class from the
Forty-fourth entry's per-stock shorts: confirmed live and currently
listed via `https://api.kite.trade/instruments` (the Twenty-ninth entry's
no-auth-needed instrument master) — `NIFTY26SEPFUT`/`OCT`/`NOV`,
`lot_size=65`, `NFO-FUT` segment — so this doesn't carry the Forty-fourth
entry's unmodeled SLB/short-selling-availability caveat; index futures
are a standard, directly shortable instrument.

Implemented as `probe_ibs_rotation_hedged.py`: `simulate_hedged()` runs
the unmodified long-only IBS rotation (`rank_by_ibs()`, reused directly
from `probe_ibs_rotation.py`) for the stock-picking leg, then each month
adds a short NIFTY notional position sized at `hedge_ratio x beta x
current_capital`, marked to market against NIFTY's own realized monthly
return, costed at 0.05% round-trip (this project's own established
figure for index-futures STT/brokerage vs. equity delivery's 0.2%, from
the "Sixth" entry). `lot_feasibility()` checks the resulting notional
against a live-fetched NIFTY spot price and the confirmed 65-unit lot
size for real capital feasibility, the same discipline every commodity/FX
capital check in this project has applied since the Twenty-fifth entry.

**Regression beta = 1.143** (long-only strategy moves slightly more than
1:1 with NIFTY on average) — cross-checks cleanly against the
Forty-fourth entry's own correlation figure (0.795 recomputed here vs.
0.796 there, same data, same result, confirming no drift between the two
entries' otherwise-independent implementations).

**Backtest result: clean, and a real improvement in risk-adjusted terms**
— full beta-hedge (hedge_ratio=1.0) cuts max drawdown from the long-only
baseline's 38.2% to **25.5%** while keeping a real **8.19%/yr** return
(vs. long-only's 22.11%/yr — a meaningful cost, but not the near-total
destruction the Forty-fourth entry's per-stock short caused). A half
hedge (0.5x) is a real, different point on the same tradeoff curve:
15.52%/yr at 23.6% drawdown — notably, LOWER drawdown than the full hedge
in this specific run, plausibly because a partial hedge avoids the full
hedge's own added cost/overcorrection in some months; not investigated
further since the feasibility check below closes the line regardless.
**Walk-forward is consistent** (in-sample 5.50%/yr at 16.5% DD,
out-of-sample 10.73%/yr at 14.8% DD, both positive) and **all 4 quarters
are positive** (8.05% / 2.86% / 5.30% / 16.34%/yr, Q4 the strongest — no
decay) at the full hedge ratio. This is a real, well-behaved backtest by
every check this project applies.

**But it's blocked by the identical capital-tier wall this project has
already closed out for every other Nifty derivative (Sixth/Seventh/Ninth
entries)**: at live-fetched NIFTY levels (~23,291), one 65-unit lot's
notional is ~₹1,513,905, needing ~₹196,808 margin (13% SPAN+exposure, the
standard index-futures figure) just to hold ONE lot — **2-6.5x this
project's entire ₹30,000-100,000 target capital range**, before even
getting to the beta-appropriate fraction of a lot the hedge actually
calls for (0.08 lots at ₹100,000 capital — not tradable at any whole-lot
granularity). This is the same wall, re-derived independently via a live
API fetch rather than recalled figures, that already closed the iron
condor (Seventh/Ninth entries) and the single-sided put credit spread —
SEBI's market-wide minimum-contract-value floor applies to every Nifty
derivative, hedge overlay included, not just the option-selling
strategies that found it first.

**Net verdict**: forty-fifth entry, and a genuinely different outcome
from the Forty-fourth entry's blunt rejection — this hedge construction
is mechanically sound and backtest-clean (unlike the per-stock short,
which failed on its own merits before capital was even a question). It's
closed for the same structural reason as options-selling, not for a
signal-quality reason: real edge, right construction, wrong capital tier.
**The long-only construction (Thirty-eighth through Forty-first entries)
remains this project's strongest, only currently-actionable finding.**
No mechanism has yet cleared the bar to actually trade.

## Forty-sixth: substituting a NIFTYBEES ETF short for NIFTY futures routes around the capital-tier wall — and finds a genuinely better result than either prior hedge attempt, at a HALF beta-hedge ratio

Direct follow-up on the Forty-fifth entry's own close: that entry's NIFTY-
futures beta hedge worked cleanly in backtest (drawdown 38.2%→25.5%,
8.19%/yr, walk-forward consistent, all 4 quarters positive) but was
blocked by the same capital-tier wall as every other Nifty derivative in
this project — one 65-unit futures lot needs ~₹196,800 margin, 2-6.5x this
project's ₹30,000-100,000 target capital. This entry substitutes the hedge
INSTRUMENT, keeping the identical beta-sizing logic: `NIFTYBEES.NS`
(Nippon India ETF Nifty 50 BEES), confirmed live via
`https://api.kite.trade/instruments` — NSE, `EQ` segment, `lot_size=1`, an
ordinary equity share rather than a fixed derivative lot. Unlike a futures
lot, an ETF position can be sized to ANY whole-share quantity, so the
hedge notional can match whatever capital actually allows.

`probe_ibs_rotation_etf_hedge.py` reuses the Forty-fifth entry's exact
beta-regression logic (`compute_beta` from `probe_ibs_rotation_hedged.py`,
unchanged — beta=1.143, cross-checks identically since it's the same
long-only strategy and NIFTY series) and the unmodified long-only stock
picking (`rank_by_ibs`), adding a short `NIFTYBEES.NS` leg sized at
`hedge_ratio x beta x capital`, rounded DOWN to whole shares (`math.floor`
— the entire point of substituting the instrument). Same SLB/short-
selling-not-modeled caveat the Forty-fourth/Forty-fifth entries already
flagged for any month-long NSE cash-equity short applies here too, noted
explicitly rather than assumed away.

**Data check first, since an ETF is a real fund with its own listing
history and drift, not a synthetic proxy**: `NIFTYBEES.NS` has 2,473 daily
bars from 2016-09-19 through today, matching the 10y backtest window
exactly (no truncation issue this time, unlike a risk this entry's own
directive specifically flagged checking for). But it is NOT a pure 1:1
price proxy for `^NSEI` — over the common window NIFTYBEES returned
+196.2% total vs. `^NSEI`'s +164.3%, a 31.9-percentage-point gap. This is
a real, expected structural difference, not a data bug: `^NSEI` is a
PRICE index (excludes dividends), while `NIFTYBEES` is a fund that
actually holds the underlying 50 stocks and accrues their real dividend
income into its NAV over time. **This matters directly for a SHORT
hedge**: shorting the ETF means bleeding that extra dividend-driven
appreciation on top of ordinary index moves, a drag a NIFTY futures short
doesn't carry the same way.

**Capital feasibility: this is the first Nifty-linked hedge in this
project to clear the capital-tier wall outright.** At `NIFTYBEES.NS`'s
live price (~₹265.84/share), a full beta-hedge at ₹30,000 capital needs
128 whole shares (₹34,283 notional) — fully achievable; at ₹100,000
capital, 429 shares. No fractional-lot problem anywhere in this project's
target range, unlike every Nifty futures/options construction tried
before it.

**Backtest result confirms the dividend-drag prediction directly, and
finds a genuinely better hedge ratio than either prior attempt**:

| Construction | Full period | Max DD | Walk-forward | Quarters positive |
|---|---|---|---|---|
| Unhedged long-only (Thirty-ninth) | 22.12%/yr | 38.2% | consistent | 4/4 |
| NIFTY-futures full hedge (Forty-fifth) | 8.19%/yr | 25.5% | consistent | 4/4 |
| **NIFTYBEES full hedge (1.0x, this entry)** | **1.61%/yr** | 30.1% | **INCONSISTENT** (-0.81%/yr in-sample, +4.14%/yr out) | 2/4 (Q2 -3.43%, Q3 -0.76%) |
| **NIFTYBEES half hedge (0.5x, this entry)** | **12.01%/yr** | **24.2%** | **consistent** (+11.93%/yr in-sample, +11.71%/yr out) | **4/4** (+13.14% / +10.47% / +8.46% / +14.90%/yr) |

The FULL ETF hedge underperforms the futures full hedge badly (1.61%/yr
vs. 8.19%/yr) and fails walk-forward/quarter-split outright — direct
confirmation of the dividend-drag mechanism predicted above: over-hedging
with an instrument that structurally drifts up faster than the raw index
bleeds return on every month the short leg is open, and 10 years of that
drag adds up to a lot more than the extra ~32pp gap alone would suggest
once compounded monthly against a levered (beta=1.143) notional. The HALF
hedge is a different, much better story: it keeps most of the drawdown
reduction (24.2% vs. the futures full hedge's 25.5% — comparably good),
returns MORE than the futures full hedge (12.01%/yr vs. 8.19%/yr), is
walk-forward consistent, and is the first hedge construction in this
project's history with all 4 quarters positive AND real capital
feasibility at both ends of the target range simultaneously.

**Net verdict**: forty-sixth entry, and — unlike the Forty-fourth entry's
clean rejection — a genuinely positive, if unexpected, result: not "use
more hedge for more safety" (the naive full-beta version this entry set
out to test) but "a PARTIAL hedge, sized to route around the ETF's own
dividend-drag drift, outperforms both the unhedged baseline's drawdown and
the fully-hedged NIFTY-futures construction's return — and is the first
Nifty-beta hedge in this project's history that's actually capital-
feasible." The mechanism (ETF total-return drift vs. price-index-based
beta sizing) is itself a reusable lesson: any future hedge or pairs
construction using an ETF as an index proxy in this project should size
against the ETF's OWN historical beta/volatility, not the underlying
price index's, or deliberately under-hedge as done here. **Still not
declared tradable** — the SLB/short-availability caveat remains unmodeled,
and this is a hedge OVERLAY on the Thirty-eighth through Forty-first
entries' long-only finding, not a new standalone strategy; the underlying
survivorship-bias and forward-validation gaps already flagged for the
base strategy still apply unchanged. **The long-only construction remains
this project's primary, most rigorously corroborated finding** — this
entry adds a real, capital-feasible, walk-forward-consistent risk-
reduction option on top of it (half-hedge: keep ~54% of the return,
cut drawdown by a third) rather than replacing it. No mechanism has yet
cleared the bar to actually trade.

## Forty-seventh: is the half-hedge ratio robust, or a lucky single point? A proper sweep confirms it's genuinely robust

The Forty-sixth entry only tested three points (0%, 50%, 100% hedge) and
found 50% was the clear winner — but this project's own established rigor
bar (the Thirty-fourth entry's regime-threshold single-point-fit trap, the
Fifteenth/Seventeenth entries' `length`/`st_period` cliffs) requires
perturbing any chosen parameter on a finer grid before trusting it, not
just accepting the best of three coarse options. This entry is that check,
not a new mechanism.

Extended `probe_ibs_rotation_etf_hedge.py` with `ratio_sweep()` (`--ratio-
sweep`) — same beta (1.143), same universe/cost model, computing
full-period return, max drawdown, walk-forward consistency, and quarter-
split at each ratio, reusing `simulate_etf_hedged()` unchanged.

**Full grid, 0.25 through 1.0**:

| ratio | return/yr | max DD | walk-forward (in/out) | consistent | quarters+ |
|---|---|---|---|---|---|
| 0.250 | 17.11% | 31.3% | +18.31% / +15.44% | True | 4/4 |
| 0.375 | 14.57% | 27.8% | +15.12% / +13.57% | True | 4/4 |
| **0.500** | **12.00%** | **24.2%** | +11.93% / +11.70% | True | **4/4** |
| 0.625 | 9.43% | 24.4% | +8.73% / +9.81% | True | 4/4 |
| 0.750 | 6.84% | 26.3% | +5.54% / +7.93% | True | 4/4 |
| 0.800 | 5.80% | 27.0% | +4.26% / +7.18% | True | 4/4 |
| 0.875 | 4.23% | 28.1% | +2.35% / +6.03% | True | 4/4 |
| 0.900 | 3.70% | 28.4% | +1.71% / +5.65% | True | 3/4 |
| 0.950 | 2.66% | 29.2% | +0.45% / +4.89% | True | 3/4 |
| 1.000 | 1.60% | 30.1% | -0.82% / +4.12% | **False** | 2/4 |

**Return declines smoothly and monotonically across the entire range** —
no cliffs, no reversals, exactly the Davey-style robustness this project
trusts. **Drawdown traces a smooth U-shape**, bottoming at 0.50-0.625
(24.2%/24.4%) before rising back toward the unhedged baseline's dividend-
drag-driven degradation as the ratio approaches 1.0 (matching the
Forty-sixth entry's full-hedge finding exactly). **Consistency doesn't
break at some arbitrary interior point — it degrades gradually starting
only at 0.90** (3/4 quarters), and fully fails only at the full hedge
(1.0: walk-forward INCONSISTENT, 2/4 quarters) — the same failure point
the Forty-sixth entry already found, now confirmed to be the edge of a
real cliff rather than an isolated bad draw.

**0.50 is not a single-point fit.** Every ratio from 0.25 through 0.875 —
a wide 0.625-wide band — passes every check (walk-forward consistent, all
4 quarters positive). 0.50 sits at essentially the drawdown-minimizing
point within that robust band, which is why the Forty-sixth entry's coarse
three-point test happened to land on it, but the surrounding grid confirms
it's a genuine local optimum on a smooth curve, not a fragile peak the way
the Fifteenth/Seventeenth entries' `length`/`st_period` defaults turned
out to be.

**Net verdict**: forty-seventh entry, and a clean confirmation rather than
a correction — the Forty-sixth entry's headline 0.50 hedge ratio survives
the perturbation check this project's own methodology requires before
trusting a chosen parameter. A defensible recommended range is **0.375-
0.625** (all three pass cleanly, drawdown-minimizing region), not a single
exact value — README updated to reflect the range rather than implying
0.50 is uniquely correct. **The long-only construction (Thirty-eighth
through Forty-first entries) remains this project's primary finding; this
entry's half-hedge overlay (or any ratio in the 0.375-0.625 range) remains
a real, capital-feasible, robust risk-reduction option on top of it.** No
mechanism has yet cleared the bar to actually trade.

## Forty-eighth: forward paper-tracking for the hedged strategy — a second, parallel live record now running alongside the unhedged one

The Forty-first entry built forward tracking for the long-only IBS
rotation. The Forty-sixth/Forty-seventh entries then established a
materially different, also-real risk profile (half-hedge: ~54% of the
unhedged return, ~a third less drawdown, capital-feasible, robust across
0.375-0.625) — different enough that it deserves its own independent
out-of-sample record starting now, not folded into or substituted for the
existing one.

`paper_track_ibs_rotation_hedged.py` — same monthly cadence and
append-only-log convention as `paper_track_ibs_rotation.py` (entry 41),
extended with a short `NIFTYBEES.NS` leg at `HEDGE_RATIO=0.5` (the
Forty-seventh entry's drawdown-minimizing point inside the validated
0.375-0.625 band). Reuses `rank_by_ibs`/`dates_closes_maps`/
`fetch_calendar`/`price_at_or_before`/`month_end_dates` from
`probe_ibs_rotation.py`, `compute_beta` from `probe_ibs_rotation_hedged.py`,
and `ETF_SYMBOL`/`ETF_COST_PCT`/`fetch_etf_series` from
`probe_ibs_rotation_etf_hedge.py` — no picking or hedge-sizing logic is
reimplemented, only orchestrated live instead of over historical bars.
Beta is recomputed fresh each run from the full 10y monthly-return
regression (not hardcoded to the 1.143 the Forty-fifth/Forty-sixth/
Forty-seventh entries measured on past data) so the live hedge notional
stays honest as more data accumulates. 3 new tests
(`tests/test_paper_track_ibs_rotation_hedged.py`): hedged and unhedged
live pickers select identically on the same data, hedge-quantity sizing
matches the backtest's own `floor(hedge_ratio * beta * capital / price)`
formula, and `mark_to_market()`'s combined P&L matches
`simulate_etf_hedged()`'s P&L to the cent on an identical single-period
test. Full suite 167/167 green.

**First real forward record, logged 2026-09-17**: `POWERGRID.NS`
(entry_ibs=0.1298, ₹264.20), `RELIANCE.NS` (0.1721, ₹1244.10),
`HINDUNILVR.NS` (0.2132, ₹1951.80), `TCS.NS` (0.2221, ₹2202.80),
`MARUTI.NS` (0.2606, ₹12378.00); short `NIFTYBEES.NS` 214 shares @
₹265.94 (beta=1.143, hedge_ratio=0.5, hedge notional ₹57,145).

**Worth flagging honestly, not hiding**: this run's stock picks differ
from the unhedged tracker's own same-day log entry (`BAJFINANCE.NS`
swapped for `TCS.NS`) — not a bug, but the Thirty-eighth entry's own
already-documented intermittent yfinance retry variance, confirmed here
because the two trackers' separate fetch calls (run minutes apart within
the same session) pulled marginally different price data. The regression
test confirms both pickers agree exactly when fed identical data; this is
a live-data-freshness artifact, not a logic divergence between the two
scripts.

**For whoever picks this up next**: two independent forward records are
now running in parallel — `paper_track_ibs_rotation_log.json` (unhedged)
and `paper_track_ibs_rotation_hedged_log.json` (half-hedged). Rerun BOTH
scripts monthly. After enough months accumulate, compare each one's real
forward return/drawdown against what its own backtest predicted — that
comparison is what will actually move this project's "no mechanism has
yet cleared the bar to actually trade" verdict, not another backtest
variant. Do not delete or hand-edit either log.

## Forty-ninth: the two live trackers' same-day pick mismatch wasn't retry noise — it was ranking against a still-forming intraday candle, fixed at the root

The Forty-eighth entry noticed its hedged tracker logged `TCS.NS` where
the Forty-first entry's unhedged tracker logged `BAJFINANCE.NS`, on the
identical date, despite both explicitly sharing `rank_by_ibs()` — and
waved it off as "the Thirty-eighth entry's already-documented intermittent
yfinance retry variance" without actually checking. That documented
phenomenon is real but different: `build_price_series()`/
`build_wide_price_series()` occasionally drop a SYMBOL entirely after 3
failed fetch retries, silently shrinking the eligible universe for that
run. This entry traces the actual cause, since these two logs are this
project's primary ongoing validation mechanism and need to be trusted.

**Reproduced directly**: called `build_wide_price_series("1y")` and
`rank_by_ibs()` twice in immediate succession — both runs agreed with
each other (52/52 symbols fetched both times, identical IBS scores) and
matched the HEDGED tracker's original picks (`TCS.NS` in, `BAJFINANCE.NS`
out) — not the unhedged tracker's original log. `BAJFINANCE.NS`'s IBS
score (0.325) isn't a near-tie at the rank-5/6 boundary either — 5th
place (`MARUTI.NS`) sits at 0.260, a real gap, ruling out "two fetches
landed on opposite sides of a genuine near-tie" as the explanation.

**Root cause, confirmed against the actual session clock**: the machine's
local time when this was investigated was 15:10 IST — NSE cash market
closes 15:30 IST, so the market was still open. Comparing the three
independent runs' own recorded values for `POWERGRID.NS` shows why:
unhedged tracker's original log recorded `entry_ibs=0.0431`, the hedged
tracker's log recorded `0.1298`, and this entry's own reproduction run
(minutes later, market now closer to close) got `0.1094` — three
meaningfully different IBS values for the same symbol, same calendar
date, because yfinance's "today" 1d candle is still live and updating
throughout the session (its close keeps moving as the session
progresses, which directly changes IBS = (close-low)/(high-low)). Two
scripts run at different wall-clock moments during market hours will
legitimately compute different rankings from genuinely different,
both-real intraday snapshots — not a bug in either script, and not a
near-tie coin flip.

**Fixed at the root, not per-caller**: added `latest_settled_date()` to
`probe_ibs_rotation.py` (the module both trackers already import from) —
returns the calendar's last date unchanged UNLESS that date is today AND
it's before 15:45 IST (a 15-minute settlement buffer past NSE's 15:30
close), in which case it falls back to the prior trading day. Both
`paper_track_ibs_rotation.py` and `paper_track_ibs_rotation_hedged.py`
now compute `as_of` through this guard instead of taking
`fetch_calendar(...)[-1]["date"].date()` directly — one shared function,
not two separate patches, so they can't independently drift on this again.
Backtests are unaffected: they only ever pass historical (already-settled)
periods, and 3 new unit tests confirm the guard is a no-op whenever the
calendar's last bar isn't "today." Full suite: 170/170 green.

**Deliberately not touched**: the two trackers' original 2026-09-17 log
entries (logged before this fix, during market hours) are left exactly as
they were written — per this project's own standing rule, forward-tracking
logs are never edited or reinterpreted retroactively, even to fix a bug
discovered right after logging. They'll self-correct through the normal
monthly mark-to-market/re-pick cycle now that the guard is in place; no
special-cased backfill was added.

**Net verdict**: a real, previously-mischaracterized bug in the
infrastructure meant to be this project's primary ongoing validation
mechanism, now fixed at its root cause rather than accepted as noise. Not
a finding about the IBS rotation strategy itself — the underlying
Thirty-eighth through Forty-eighth entries' numbers are unaffected, since
none of them ran during live market hours. **No mechanism has yet cleared
the bar to actually trade.**

## Fiftieth: a composite IBS + low-volatility cross-sectional score — a third combination shape tried, and rejected

Two prior attempts to combine independently-real signals both hurt:
AND-gating two signals into one trade's entry/exit (Eleventh/Twenty-second
entries) and running independently-real strategies as a diversified
multi-strategy portfolio (Thirty-second entry — too correlated). This
entry tries a third, genuinely different combination shape never tested:
blend IBS (this project's strongest cross-sectional finding, Thirty-eighth/
Thirty-ninth) and the low-volatility anomaly (a real academic factor,
Twenty-fourth entry, but one that decays hard in the recent Q4 2024-2026
window) into a single composite cross-sectional RANK SCORE — not an
AND-filter, not a separate capital sleeve — and use that one score as the
sort key for the same monthly-rotation structure that worked for IBS
alone. `vol_weight=0.0` reduces exactly to pure IBS rotation (kept as the
in-sweep baseline for direct comparison); `vol_weight=1.0` reduces to pure
low-vol rotation on the same universe/cost model.

Implementation: `probe_ibs_lowvol_composite_rotation.py`, reusing
`probe_ibs_rotation_widen`'s 52-stock `WIDE_UNIVERSE`/
`build_wide_price_series` and `probe_ibs_rotation`'s `avg_ibs`/
`dates_closes_maps`/`price_at_or_before`/`month_end_dates`/`fetch_calendar`
unchanged — no fetch/calendar logic reimplemented, per the Forty-ninth
entry's lesson. Composite score per stock per month: `(1 - vol_weight) *
z(avg 5-day IBS) + vol_weight * z(trailing 20-day return stdev)`, both
z-scored cross-sectionally (within that month's eligible universe) so the
two signals' different units and scales don't distort the blend; ascending
sort (lower = more oversold AND calmer). 4 new unit tests
(`tests/test_ibs_lowvol_composite_rotation.py`) confirm the z-score helper
handles degenerate (empty/constant) input and that `vol_weight=0.0`/`1.0`
correctly reduce to pure-IBS-only and pure-volatility-only ranking on
synthetic data. Full suite 174/174 green.

**Perturbation sweep (`--sweep`, 10y, full period, 5 weights x 3 top_k =
15 cells)**: no clean win anywhere. At the project's standard `top_k=5`,
`vol_weight=0.00` (pure IBS) gives 22.11%/yr at 38.2% DD (return/DD ratio
0.58) — every nonzero weight at `top_k=5` gives BOTH lower return AND
comparable or only modestly lower drawdown (`vol_weight=1.00`: 14.93%/yr
at 25.2% DD, ratio 0.59, essentially a wash, not an improvement). The best
raw return/DD ratio in the whole grid (0.67) appears at `vol_weight=0.50,
top_k=8` — but `top_k=8` is more generous than `top_k=5` for every weight
tested (including pure IBS: 20.02%/yr at 31.7% DD there too), so this is a
`top_k` effect, not evidence the composite itself helps; comparing at
matched `top_k` throughout, blending in volatility never clearly wins.
One real warning sign: `vol_weight=1.00, top_k=8` hits a NEGATIVE Q4 —
the exact recent-quarter decay signature the Twenty-fourth entry's
standalone low-vol anomaly already showed, creeping back in once
volatility dominates the score even inside this otherwise-robust rotation
shape.

**Walk-forward (the decisive check)**: pure IBS at `top_k=5`
(`vol_weight=0.00`) posts 24.59%/yr in-sample and a strong **19.12%/yr
out-of-sample**. The two composite candidates that looked most
competitive in the full-period sweep collapse out-of-sample instead:
`vol_weight=0.50, top_k=5` goes from 21.19%/yr in-sample to **7.59%/yr**
out-of-sample; `vol_weight=0.25, top_k=8` goes from 28.09%/yr in-sample to
**9.38%/yr** out-of-sample. Both stay sign-consistent (still positive), so
neither fails the walk-forward gate outright, but both give up roughly
60% of their out-of-sample return relative to pure IBS at the same
top_k — a materially worse recent-period result, not a wash.

**Significance** (`--significance`, 1,000-seed random-top-k control,
identical to the Thirty-ninth entry's methodology): `vol_weight=0.50,
top_k=5` scores `percentile=64.0, empirical_p=0.36, z=0.17` —
statistically indistinguishable from picking 5 random stocks each month.
Pure IBS at the same `top_k=5` on the same universe scored `p=0.015`
(Thirty-ninth entry). Blending in volatility doesn't just fail to help —
it destroys the statistical significance that was this project's main
evidence IBS rotation isn't a lucky draw.

**Net verdict: rejected.** All three checks (full-grid sweep, walk-forward,
significance) agree in direction: composite ranking never beats plain IBS
rotation at matched `top_k`, degrades badly out-of-sample specifically
(not just a return/risk trade-off), and destroys statistical significance
at a plausible middle weight. The likely mechanism: low-volatility's real
edge comes with its own Q4-decay weakness (Twenty-fourth entry), and
blending it into the score doesn't get "the good part of low-vol without
the bad part" — it dilutes IBS's real, mean-reversion-driven, still-Q4-
robust edge while reintroducing exactly the regime fragility this test
hoped to avoid. Sizing-up check intentionally skipped, consistent with
every prior rotation entry (Thirty-eighth through Forty-ninth) — this is
an equal-weight top-k rotation, not a per-trade ATR-stop sizing strategy,
so the `--risk-per-trade-pct` dilution check doesn't apply to this shape.
**The long-only plain IBS rotation (Thirty-eighth through Forty-first
entries) remains this project's sole standing finding; 50 mechanisms
tested, no mechanism has yet cleared the bar to actually trade.**

## Fifty-first: an adversarial council review of the IBS rotation finding — the project's own "not tradable yet" caution turns out to still be an UNDERSTATEMENT

Three independent reviewers (no shared context, no access to each other's
findings or this project's own hedged framing) were asked to critically
audit the IBS rotation finding from different angles: statistical rigor,
data/execution realism, and devil's-advocate red-teaming. All three
converged on a harder verdict than this project's own repeated "no
mechanism has yet cleared the bar" disclaimer implies — not that the
disclaimer is wrong, but that even it may be under-stating how far this
finding is from tradable.

**Statistical rigor**: the reported significance (Thirty-ninth entry's
p=0.014-0.037) is a single-config p-value computed AFTER the config was
selected from ~25+ internal parameter/universe variants tried within the
IBS-rotation line itself, on top of the 50-mechanism search that produced
this line in the first place. A Bonferroni-style family-wise correction
across just the 50 mechanisms requires p<0.001 for alpha=0.05 — the
reported values miss that by 1-2 orders of magnitude, and no
multiple-testing correction has ever been applied anywhere in this
project's write-ups. Separately: `rank_by_ibs()` ranks using the *same
day's* candle that `price_at_or_before()` then fills at — an implicit
same-bar, zero-latency fill assumption that isn't achievable live (this
is structurally the same class of bug the Forty-ninth entry had to patch
for the live tracker, except that fix only covers the live scripts, not
the backtest itself). The walk-forward/quarter-split check is real and
has killed 30+ other mechanisms, but here it isn't a true held-out test —
the top_k/lookback grid and the universe size were themselves chosen by
looking at performance on the exact same historical window being
"validated."

**Data/execution realism**: `build_price_series()`'s silent-empty-on-
failed-fetch behavior can silently shrink the eligible universe for the
ENTIRE backtest window, not just one month — plausibly the same mechanism
already caught behaving non-deterministically in the Fortieth entry (top-3
concentration 44.0% vs 50.4% on reruns of identical config/data). No
slippage or bid-ask spread term exists anywhere in the cost model
(`simulate()` charges a flat 0.2% + Rs16 and fills at the exact recorded
close) — for a strategy whose entire selection criterion is "just closed
near today's low," assuming a clean fill at that exact close is close to
the least defensible assumption available. The Thirty-ninth entry's
whole-share-rounding gap (MARUTI.NS, BAJAJ-AUTO.NS, ULTRACEMCO.NS) is
still unfixed in the simulator, which still assumes continuous notional.

**Red-team**: the Fortieth entry's survivorship stress test is being
credited as "the strategy survives real historical blowups," but the
actual numbers (JETAIRWAYS/YESBANK/RCOM/PCJEWELLER — 3 of 4 becoming
top-3 contributors, PCJEWELLER alone the single best contributor in the
whole 56-stock universe, concentration WORSENING to 50.4%) are direct
confirmation of bounce-harvesting, not evidence against it — and the two
genuine zero-recovery delistings (DHFL.NS, RELCAPITAL.NS) are invisible to
yfinance, so the stress test could only ever be run on crashes that
happened to keep trading. The "Cross-mechanism synthesis" (written before
entry 38) already predicted single-instrument/short-horizon mean-reversion
would outperform in this exact recent window — meaning IBS rotation is the
first mechanism tried AFTER a hypothesis was already formed about what
shape should win, a textbook regime-fluke-with-retrofitted-story risk that
no check in this project's methodology is designed to catch. Also flagged:
an asymmetric rigor pattern — IBS's own disconfirming candidates (short
leg, composite score) got the full multi-check treatment before rejection,
while OTHER mechanisms' stress tests (e.g. Forty-third's breakout/momentum
rotation survey) stopped at the first walk-forward failure.

**Net verdict**: no reviewer would deploy capital on this basis. This
doesn't overturn the finding — IBS rotation remains the most rigorously
tested candidate this project has produced, and the honest per-entry
write-ups are why the gaps above were even findable. But "no mechanism has
yet cleared the bar" should be read as true with room to spare, not as
false modesty on an otherwise-ready result. **Concrete bar for next time,
per all three reviewers**: (1) report significance against a
multiple-comparisons-corrected threshold, not a raw single-config p-value;
(2) add a real slippage/spread cost term before trusting the return
numbers, especially for the specific "just cratered" stock bucket this
strategy selects; (3) treat the Fortieth entry's bounce-harvesting finding
as a live risk to size around, not a passed stress test; (4) wait for
multiple real months of both paper trackers' forward data (currently 1
day each, as of 2026-09-17) before revisiting tradability — no backtest
refinement substitutes for that. No mechanism has yet cleared the bar to
actually trade.

## Fifty-second: adding a slippage cost term to the IBS rotation backtest — robust to slippage MAGNITUDE under a symmetric assumption, but this specific check cannot rule out the asymmetric risk the council review actually raised

Correction to this entry's own original heading, per a follow-up adversarial
review (two judges, statistical-adequacy and implementation-correctness):
the first version of this section led with "the finding survives, at every
level tested" — true of the number, but read on its own it overclaims
relative to the caveat below, which is the entry's actual main finding, not
a footnote to it. Re-headed for that reason; the body and numbers are
unchanged.

Direct closure of the first of the Fifty-first entry's two concrete asks
("add a real slippage/spread cost term before trusting the return
numbers"). Until now, `probe_ibs_rotation.py`'s cost model (and every
script built on it — `_widen`, `_significance`, `_survivorship`, both
paper trackers) charged only 0.2% STT+stamp per leg plus a flat ₹16 DP
charge, filling at the exact recorded close — no separate slippage/spread
term existed anywhere, despite this strategy's entry criterion
("just closed near today's low") being close to the least defensible case
for assuming a clean fill at the reference price.

Added `slippage_pct` (default 0.0, byte-for-byte unchanged old behavior)
to `simulate()` (`probe_ibs_rotation.py`), `simulate_wide()`
(`probe_ibs_rotation_widen.py`), and `simulate_random()`
(`probe_ibs_rotation_significance.py`) — a symmetric per-leg fill-price
haircut, buying at `entry_px * (1 + slippage_pct/100)` and selling at
`exit_px * (1 - slippage_pct/100)`, layered on top of the existing
STT/stamp/DP cost, not replacing it. Applied identically to both the real
IBS-ranked picks and the random-control draws in the significance test,
for a fair comparison — flagged explicitly as itself a simplification
below. Full suite (174 tests) unaffected and still green; no new pytest
file per this project's established "probe scripts don't get one"
convention — verified instead by confirming `slippage_pct=0.0` reproduces
the pre-existing numbers exactly (the formula reduces to the untouched
`(exit_px - entry_px) / entry_px` when the multiplier is 1.0).

**Swept 0%, 0.05%, 0.1%, 0.2%, 0.3%, 0.5% per leg on the canonical 52-stock
`WIDE_UNIVERSE`, `top_k=5, lookback=5`, full period + walk-forward +
quarter-split** (10y):

| slippage/leg | full-period | walk-forward (in/out) | consistent | quarters+ |
|---|---|---|---|---|
| 0.00% | 22.13%/yr | 24.61% / 19.15% | Yes | 4/4 |
| 0.05% | 20.64%/yr | 23.10% / 17.69% | Yes | 4/4 |
| 0.10% | 19.17%/yr | 21.60% / 16.26% | Yes | 4/4 |
| 0.20% | 16.28%/yr | 18.65% / 13.44% | Yes | 4/4 |
| 0.30% | 13.44%/yr | 15.77% / 10.68% | Yes | 4/4 |
| 0.50% | 7.95%/yr | 10.22% / 5.36% | Yes | 4/4 |

**Return decays smoothly and monotonically with no cliffs** — real
Davey-style robustness to this specific cost dimension, holding all the
way out to 0.50% per leg (a full 1.0% round-trip slippage ON TOP of the
existing 0.4% STT+stamp round trip + flat DP charge — a genuinely
aggressive stress level for liquid NSE names, not a realistic central
estimate). Walk-forward stays consistent and all 4 quarters stay positive
at every single level tested, including Q4 2024-2026 (the most recent
window): +17.24%/yr at 0.10% slippage, +11.67%/yr even at the 0.30% stress
level — no decay reappears.

**Re-ran the Thirty-ninth entry's 1,500-seed significance test at 0.10%
(a realistic estimate) and 0.50% (stress) per leg, `top_k=3/5/8`**: the
empirical p-values and z-scores came back **essentially unchanged** from
the zero-slippage originals at every portfolio size (`top_k=5`:
p=0.0153/z=2.74 at 0.10% slippage and p=0.0153/z=2.73 at 0.50%, vs.
the Thirty-ninth entry's own p=0.0153/z=2.75 at zero slippage). This
isn't a coincidence or a bug — it's the direct mathematical consequence
of applying the SAME slippage assumption, on the SAME number of monthly
trades, to both the real strategy and every random-control draw: slippage
shrinks both distributions by a similar proportional amount, so the
actual strategy's PERCENTILE within the random-control distribution barely
moves even though its absolute return drops hard (top_k=5's actual final
capital fell from ₹731,663 at zero slippage to ₹574,417 at 0.10% and
₹214,431 at 0.50%).

**The caveat that matters more than the result**: this test can only speak
to what happens when actual and random draws face the SAME slippage
assumption — it does not, and by construction cannot, test the more
concerning possibility the Fifty-first entry itself raised: that IBS's
specific selection criterion (a stock that just closed at today's low,
often after a large one-day move) could carry systematically WORSE
slippage than an average/random stock on an average day, because a
larger, more volatile move is more likely to coincide with a wider
spread. If that asymmetry is real, the true degradation to the actual
strategy specifically would be larger than modeled here, while the random
control's would not — which would erode both the return AND (unlike this
symmetric test) the statistical significance. Not modeled, because no
real bid-ask/order-book data source is available to this project's stack
(the same practical limitation the Fourteenth entry's iron-condor
real-data validation ran into for a different instrument class) —
flagged as an open, unresolved possibility rather than dismissed.

**Net verdict**: fifty-second entry, and one of this project's flagged
validation gaps closes cleanly — the IBS rotation finding is robust to
slippage magnitude under a fair, symmetric assumption, all the way past
any plausible realistic level and well into deliberately unrealistic
stress territory. But this specific check cannot rule out — and by its
own construction never could rule out — the asymmetric-slippage risk the
Fifty-first entry actually raised (the "just cratered" bucket facing worse
fills than an average stock, not just any fills being worse than the
model). The Fifty-first entry's OTHER flagged item (report significance
against a multiple-comparisons-corrected threshold across the ~25+
internal configs and 50+ mechanisms tried) remains open, untouched by
this entry. Also flagged, not fixed: `slippage_pct` was added only to the
backtest/significance probes — `paper_track_ibs_rotation.py` and
`paper_track_ibs_rotation_hedged.py` still run a zero-slippage cost model,
so the two live forward records are not yet consistent with this entry's
own more conservative backtest assumptions. **The long-only IBS rotation
(Thirty-eighth through
Forty-first entries) remains this project's primary finding, now with one
more rigor check closed in its favor — not yet enough to call it
tradable.** No mechanism has yet cleared the bar to actually trade.

## Fifty-third: closing the multiple-comparisons gap — none of this project's reported significance survives correction once the real search family is counted

Direct closure of the Fifty-first entry's other still-open ask ("report
significance against a multiple-comparisons-corrected threshold, not a raw
single-config p-value"). Every p-value this project has ever reported
(Thirty-ninth, Forty-second, Fiftieth, Fifty-second entries) was computed
and read in isolation, with no accounting for how many hypotheses were
actually evaluated with that exact methodology before one of them got
reported as "significant."

Added `multiple_comparisons.py`: `bonferroni()` (family-wise, strict —
alpha/m) and `benjamini_hochberg()` (false-discovery-rate, the standard,
less punishing choice for an exploratory sweep like this project's),
applied side by side since neither alone is "the" right answer for an
after-the-fact research audit. An assert-based self-check (a p-value far
below the corrected threshold must survive both procedures; one far above
must survive neither, at any family size) stands in for a pytest file, this
project's established convention for a script that isn't itself a backtest.

**Two honestly different family definitions, not one**, because the right
denominator is itself ambiguous and picking only one would smuggle a
conclusion in by the choice of scope:

- **Narrow (m=3)**: exactly the 3 portfolio sizes
  `probe_ibs_rotation_significance.py`'s own docstring says it exists to
  check ("the edge isn't itself a lucky parameter pick") — the Thirty-ninth
  entry's own stated scope, nothing broader.
- **Broad (m=7)**: every p-value this project's `--significance` check has
  EVER been run against, across every cross-sectional candidate that
  methodology was applied to while searching for something to report — IBS
  rotation's own 3 sizes, RSI-2 rotation's 3 sizes (Forty-second entry), and
  the IBS+low-vol composite (Fiftieth entry) — not just the one that
  eventually got kept. This is the family the Fifty-first entry's council
  review actually objected to being ignored.

All 7 p-values re-derived fresh in one sitting (2026-09-18, `--n-seeds
1500`, same data snapshot) rather than quoting the individually-logged
numbers scattered across three different sessions' CLAUDE.md entries — the
Fortieth/Forty-eighth entries already documented that yfinance's per-symbol
retry behavior can shift results slightly run-to-run, so mixing numbers
from different sessions would be its own small methodological sin. The
rerun reproduced the Thirty-ninth entry's own numbers almost exactly
(top_k=5: p=0.0160 vs. the original 0.0153; top_k=8: p=0.0140 vs. 0.0140
exactly; top_k=3: p=0.0367 vs. 0.0367 exactly) — the measurement itself is
stable, only the correction applied to it is new.

**Narrow family (m=3, Bonferroni threshold 0.01667)**: `top_k=5`
(p=0.0160) and `top_k=8` (p=0.0140) both still clear it; `top_k=3`
(p=0.0367) does not. Under Benjamini-Hochberg at the same m=3, all three
survive (the FDR procedure's own step-up rule licenses this once the
largest p-value, 0.0367, clears its own rank-3 critical value of
3/3 x 0.05 = 0.05). Read narrowly — did IBS rotation's own portfolio-size
sweep find a real effect and not a lucky size? — the answer is still
"mostly yes," largely unchanged from the Thirty-ninth entry's own
uncorrected read.

**But m=3 is not the honest narrow family — the Fifty-first entry already
told us what is, and this section originally failed to check its own
work against it.** A follow-up adversarial review of this entry (two
judges) caught the asymmetry directly: the Fifty-first entry's own text
already states the real internal search was "~25+ internal parameter/
universe variants tried within the IBS-rotation line itself" (the
top_k x lookback grid in the Thirty-eighth entry, +6 more widening the
Thirty-ninth, +3 more in the Fortieth) before the reported config was
settled on — not 3. Re-run at **m=25** (Bonferroni threshold 0.002,
`multiple_comparisons.py`'s new `IBS_INTERNAL_SEARCH_FAMILY_SIZE`; BH
isn't computable at this m since the other ~22 variants were never each
assigned a p-value, only a config choice): **IBS rotation's own best
p-value (0.0140) fails outright** — roughly 7x above the corrected
threshold. The "narrow family still stands" framing this section
originally led with was itself the cherry-picked half, not the broad
family's harsher conclusion — swapping in a friendlier, smaller m for the
"favorable" side of the comparison while treating m=7 as the rigorous
"broad" one. Corrected here rather than left standing next to the m=7
result as if the two were equally representative.

**Broad family (m=7, Bonferroni threshold 0.00714)**: **zero of the seven
p-values survive**, under EITHER Bonferroni or Benjamini-Hochberg. Even
IBS rotation's own best case (`top_k=8`, p=0.0140) is roughly double the
Bonferroni-corrected threshold, and BH's step-up rule never finds a rank
where the sorted p-values dip below their own critical line (rank 1's own
p=0.0140 already exceeds its critical value of 1/7 x 0.05 = 0.00714, which
is enough on its own to zero out the whole procedure, since BH requires at
least the smallest p-value to clear rank 1's threshold before any larger
rank can be included).

**Net verdict**: this is exactly the "harder verdict" the Fifty-first
entry's council review said this project's own caution understated, and
it turns out to be harder than this entry's own first pass initially
credited, too. The ONLY reading under which IBS rotation's significance
survives is the artificially narrow m=3 (just its own 3 portfolio sizes)
— and that reading doesn't actually match this project's real search
process, per the Fifty-first entry's own already-established count of
~25 internal variants tried within this exact line. At that honestly-scoped
m=25, IBS rotation's own best p-value fails outright, by roughly 7x.
Combined with the m=7 broad-family result (also zero survivors), there is
now no family-size scoping — narrow-and-honest, or broad-and-inclusive —
under which this project's flagship finding clears a corrected
significance bar. This doesn't newly disprove IBS rotation as a real
effect (a Bonferroni/BH correction bounds false-discovery risk under
formal search-space accounting, it doesn't prove the null); it means the
specific "p<0.05, corroborated" claim this project has repeated since the
Thirty-ninth entry should be retired, not requalified with a favorable
family size. **Deliberately not extended to the
project's full 50+-mechanism search**: those were overwhelmingly screened
by walk-forward/quarter-split hit rate, not by this random-control p-value
methodology, so most have no p-value to correct in the first place — a
Bonferroni-style correction can only be applied to the family of tests that
actually produced a p-value, not asserted as a single deflated number
across every mechanism this project has ever tried. **The long-only IBS
rotation (Thirty-eighth through Forty-first entries) remains this
project's primary finding by every OTHER check (walk-forward, quarter-split,
widening, survivorship stress, slippage robustness) — now with both of the
Fifty-first entry's concrete asks closed (slippage in the Fifty-second entry, multiple
comparisons here), and a precisely quantified answer to "does the reported
significance survive correction": no, under every honestly-scoped family
size tested (m=7 broad, m=25 narrow-but-real) — only the artificially
narrow m=3 reading (which undercounts this project's own already-documented
search) still passes.** No mechanism has yet cleared the bar to
actually trade.

## Fifty-fifth: Amihud (2002) illiquidity rotation — a genuinely different KIND of factor, and a clean rejection on concentration and significance, not decay

Per the maintainer's explicit direction after the Fifty-third entry
retired IBS rotation's significance claim: search for a NEW, genuinely
different candidate rather than keep hardening the existing one. Every
mechanism tried so far in this project is momentum (Fourth, Thirty-fifth),
mean-reversion (IBS, RSI-2, Bollinger, Turtle Soup), a volatility factor
(low-vol, Twenty-fourth), or volume-CONFIRMATION (CMF+OBV, Sixteenth) —
none is a liquidity-risk-premium factor. Amihud, "Illiquidity and stock
returns: cross-section and time-series effects" (Journal of Financial
Markets, 2002), one of the most-cited factors in empirical asset pricing:
stocks that are harder to trade without moving the price (high
price-impact-per-rupee-of-volume) earn a return premium compensating
investors for illiquidity risk.

Added `indicators.amihud_illiq()` (5 new unit tests) — mean daily
`|return| / dollar_volume` over a trailing window, needing `period + 1`
candles (each day's return needs the prior day's close, the "+1"
requirement `on_balance_volume` already has but `internal_bar_strength`
doesn't). Skips a day with non-positive volume or prior close (an index
symbol, or a genuine no-trade day) the same defensive way
`chaikin_money_flow` already does — moot for this entry specifically
since `WIDE_UNIVERSE` (`probe_ibs_rotation_widen.py`) contains no index
symbols, `^NSEI` is only ever used as the trading-day calendar via
`fetch_calendar()`, never as a tradable pick.

Implemented as `probe_amihud_rotation.py`, reusing every piece of
entries 38-53's scaffolding unchanged: `WIDE_UNIVERSE`,
`build_wide_price_series` (candle dicts already carry `volume` — no new
fetch pathway needed, per the Sixteenth entry's CMF/OBV precedent),
`fetch_calendar`, `month_end_dates`, `price_at_or_before`, and the same
STT+stamp+DP+slippage cost model. Only the ranking direction is new:
`rank_by_illiq()` sorts DESCENDING (long the most illiquid), the opposite
of `rank_by_ibs()`'s ascending most-oversold-first sort, since Amihud's
published direction is "more illiquid = more compensated," not
mean-reversion.

**Full-period and walk-forward look fine on their own**: 18.42%/yr at
47.4% max drawdown (`top_k=5, lookback=21`, the standard config), both
walk-forward halves positive (29.37%/in-sample, 7.82%/out-of-sample,
CONSISTENT). A 12-config perturbation sweep (`lookback` 10/21/42/63 x
`top_k` 3/5/8) is walk-forward-consistent in all 12 cells — no sign
flips anywhere.

**Quarter-split is a real but partial pass, and Q4 is consistently thin
at every config**: `top_k=5`/`top_k=8` have all 4 quarters positive at
every lookback tested (6/12 configs), but `top_k=3` has a NEGATIVE Q4 at
every single lookback tested (10/21/42/63 days: -9,414/-20,919/-24,357/
-19,883) — a real, config-dependent decay signature, not noise. Even
where Q4 stays positive, it's the weakest quarter by a wide margin in
every passing config (e.g. `lookback=21, top_k=5`: Q1-Q3 net 67,720/
113,373/43,282 vs. Q4's 904) — the same recent-quarter deceleration this
project's Cross-mechanism synthesis entry already associates with
basket-wide directional bets, worth flagging even though it doesn't
outright fail here.

**Attribution reveals the real problem, and it's mechanistic, not a
data artifact**: only **12 of the 52-stock universe are EVER selected**
across 119 months at the standard config — the exact same 12 names as
the Thirty-third entry's small/mid-cap universe, 10/12 individually
net-positive, but **top-3 contributors carry 67.0% of total net P&L**
(`ELGIEQUIP.NS`, `RATNAMANI.NS`, `GRAPHITE.NS`). Checked the mechanism
directly rather than assuming it: mean 60-day dollar volume is
**Rs 5.29B for the large-cap 40 vs. Rs 959M for the small/mid-cap 12 —
a 5.5x gap**. Since ILLIQ ranks descending by price-impact-per-rupee-of-
volume across the WHOLE mixed-cap universe every month, the small/mid-cap
names structurally sit at the top of the illiquidity ranking almost every
single month regardless of any genuine month-to-month liquidity dynamics
— this isn't harvesting a rotating cross-sectional signal the way IBS
rotation demonstrably does (52/52 stocks selected at least once, 73%
individually positive, Thirty-ninth entry); it's a near-static tilt
toward the same dozen smaller-cap names, dressed up as a monthly
rotation. A cleaner implementation would rank ILLIQ within market-cap
buckets or standardize by each stock's own historical ILLIQ range — not
attempted here, since the significance result below closes the line
regardless.

**Significance (1,500-seed random-control, same methodology as
`probe_ibs_rotation_significance.py`) is weak and inconsistent across
portfolio sizes, unlike IBS rotation's consistent p=0.014-0.037 across
top_k=3/5/8**: `top_k=3` p=0.590 (worse than random — z=-0.41), `top_k=5`
p=0.105 (misses even an uncorrected 0.05 bar), `top_k=8` p=0.037 (the
only one that clears an uncorrected bar, barely). Checked against
`multiple_comparisons.py`'s Bonferroni correction at Amihud's own
narrowest possible family (m=3, just its own 3 portfolio sizes, the same
generous scoping the Fifty-third entry used for IBS's narrow reading):
**0/3 survive** — even the friendliest possible reading fails outright,
unlike IBS rotation which had 2/3 survive at this same narrow scoping.
Added Amihud's 3 p-values to `multiple_comparisons.py`'s
`ALL_SIGNIFICANCE_TESTS_PVALUES` broad family (now m=10, up from m=7) for
future honest accounting — this doesn't change the broad-family verdict
(already 0/7, now 0/10), but keeps the running count of every candidate
this methodology has been applied to accurate, per the Fifty-third
entry's own established practice of not asserting a single deflated
p-value across every mechanism ever tried, only across the family that
actually produced one.

**Net verdict: rejected, and for a genuinely different reason than most
prior rejections in this project.** Not decay (Donchian/BTC, momentum
rotation, low-vol), not a lone-instrument-from-a-chance-level-sweep (IBS's
original single-instrument gold survivor, SuperTrend's oil), not a
capital-tier wall (options, commodities/FX), and not cost-drag (overnight
anomaly) — this fails because the specific ranking construction, applied
naively across a mixed-market-cap universe, collapses into a near-static
small-cap tilt rather than a genuine month-to-month cross-sectional
rotation, and that tilt's statistical significance doesn't clear even the
most generous scoping available. A real, well-cited academic factor,
correctly implemented per its published definition, still doesn't
reproduce as a usable edge on this project's universe and
infrastructure — a useful negative data point for how many of this
project's real academic-factor attempts (momentum, low-vol, now Amihud)
have failed for THREE different structural reasons (recent-quarter decay,
recent-quarter decay again, and now concentration/significance) rather
than one recurring pattern. `probe_amihud_rotation.py` stays a probe
script, no adversarial council review run (per this project's own
convention, reserved for genuinely promising results — this one fails
cleanly enough on its own checks not to need one). **The long-only IBS
rotation (Thirty-eighth through Forty-first entries) remains this
project's sole standing finding; 54 mechanisms tested, no mechanism has
yet cleared the bar to actually trade.**
