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

## Fifty-fourth: closing the whole-share-rounding gap flagged since the Thirty-ninth entry — a real, small drag at low capital, and a specific worry that didn't materialize

Direct closure of the last still-open, concrete flagged gap this project
had on hand (Fifty-first/Fifty-second/Fifty-third already closed
slippage and multiple comparisons): the Thirty-ninth entry found that
`probe_ibs_rotation.py`'s `simulate()` (and `probe_ibs_rotation_widen.py`'s
`simulate_wide()`) assumes CONTINUOUS notional per pick — at the low end
of this project's ₹30,000-100,000 target capital range, a ₹6,000
per-position slice (`top_k=5`) can't literally buy a fractional share of
an expensive name the way the simulator implicitly assumes, and flagged
this as something to fix "before paper-trading at the low end of this
project's target capital range specifically." Never actually done until
now.

Added `whole_shares` (default `False`, byte-for-byte unchanged old
behavior — verified with a direct equality assertion, not just eyeballing
similar numbers) to both `simulate()` and `simulate_wide()`: each pick's
notional is rounded DOWN to `floor(slice_capital / fill_price)` whole
shares at its actual fill price (post-slippage, so it composes correctly
with the Fifty-second entry's own `slippage_pct` option); a pick that
rounds to zero shares is skipped for the month rather than silently
assumed tradable at any fractional size, exactly as the Thirty-ninth
entry's own text described. Wired through both scripts' CLI
(`--whole-shares`) and `widen_check()`. 3 new unit tests
(`tests/test_ibs_rotation_whole_shares.py`, synthetic data): the flag
defaults off unchanged, an unaffordable pick is correctly skipped, and
whole-share P&L can never exceed the continuous-notional version's on the
same data (rounding down can only leave capital idle, never invest more).
Full suite: 188/188 green.

**Reran the exact check the Thirty-ninth entry called for — walk-forward
and quarter-split on the 52-stock `WIDE_UNIVERSE` at both ends of this
project's target capital range, `top_k=5, lookback=5`, 10y, same data
snapshot for a clean before/after**:

| capital | whole_shares | full-period annualized | max DD |
|---|---|---|---|
| ₹30,000 | False | 20.95%/yr | 38.4% |
| ₹30,000 | **True** | **19.96%/yr** | 37.6% |
| ₹100,000 | False | 22.19%/yr | 38.2% |
| ₹100,000 | **True** | **21.97%/yr** | 37.9% |

Real whole-share rounding costs roughly **1 percentage point of annual
return at ₹30,000** (20.95% -> 19.96%) and a negligible ~0.2pp at
₹100,000 (22.19% -> 21.97%) — the drag scales with how large a fraction
of each position slice ends up as un-invested leftover cash after
rounding down, which is proportionally bigger on a smaller slice. At
₹30,000 with `whole_shares=True`, walk-forward is still **CONSISTENT**
(in-sample 21.88%/yr, out-of-sample 16.19%/yr, both clearly positive) and
**all 4 quarters are still positive** (22.95% / 20.32% / 14.73% / 14.78%,
Q4 — the most recent — still positive, no decay reintroduced by the fix).

**The specific worry the Thirty-ninth entry named didn't actually
materialize in this backtest, which is itself worth recording rather than
assuming**: that entry's own text warned that `MARUTI.NS`
(~₹12,370/share) "can't buy even ONE whole share" at a ₹6,000 slice, along
with `BAJAJ-AUTO.NS`/`ULTRACEMCO.NS`/`DIVISLAB.NS`/`EICHERMOT.NS`. Checked
directly: **zero of the 120 months in the 10-year backtest, at either
₹30,000 or ₹100,000 capital, ever had a pick skipped for being
unaffordable** — none of those specific expensive names ever actually
landed in that month's bottom-5-by-IBS ranking during this window. The
~1pp/year cost at ₹30,000 comes entirely from ordinary rounding leftover
(a slice of ₹6,000 rarely divides evenly into a whole number of shares at
any price), not from missed picks — a different, smaller mechanism than
the one originally flagged, and worth knowing before assuming the fix
would show a starker effect.

**Net verdict**: the last of this project's concretely-flagged, still-open
validation gaps (Thirty-ninth's whole-share rounding, alongside
Fifty-first's slippage and multiple-comparisons asks already closed in the
Fifty-second/Fifty-third entries) is now closed. The IBS rotation finding
survives real whole-share sizing at the low end of this project's target
capital range — a small, quantified, non-disqualifying tax on return, not
a wall. This does not change this project's standing verdict: the
Fifty-third entry's multiple-comparisons finding (no honestly-scoped
family size clears a corrected significance threshold) and the
unresolved survivorship-bias gap both still apply unchanged. **No
mechanism has yet cleared the bar to actually trade.**

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
project's sole standing finding; 55 mechanisms tested, no mechanism has
yet cleared the bar to actually trade.**


## Fifty-sixth: Nifty options put-call ratio (PCR) as a contrarian positioning signal, traded long-only on NIFTYBEES — rejected: the raw result was flattered by a look-ahead fill, and the corrected one doesn't beat a matched random-timing control

Picked up from an automated Tier-2 run that stalled `blocked` before writing
anything up (worktree `pcr-signal`, never committed); its data fetch and
first probe were reused, its simulation was fixed, everything else here is
new. First signal in this project derived from OPEN INTEREST (options
positioning) rather than price, volume, or a fundamental factor. Weekly
NIFTY PCR = total put OI / total call OI across all strikes and expiries,
from real NSE F&O bhavcopy (557 weeks, 2016-01 to 2026-09, 0 fetch errors,
reusing `probe_iron_condor_real_data.py`'s fetcher; cached in
`pcr_weekly_series.csv`). "Extreme" = trailing-window percentile rank
(no lookahead), long NIFTYBEES when high PCR (fear, contrarian bullish),
exit when the percentile falls back to 0.5 or after 8 weeks. Long side
only (no SLB/short cost modeled anywhere in this project). Traded via the
ETF, so it is NOT blocked by the fixed-lot capital-tier wall that closed
every prior Nifty-derivative idea.

**Bug fixed before any number was trusted:** the inherited probe filled
at the SAME day's close as the bhavcopy. OI is published after the close,
so that is look-ahead — exactly the flaw the Fifty-first entry's council
found in the IBS backtest. Fills are now at the first close strictly after
the signal date (`lag_days=1`). Effect on the base config (entry=0.90,
window=26): 4.41%/yr -> 3.99%/yr; second-half P&L Rs 8,922 -> Rs 9,564 is
noise, but Q4 (most recent quarter) went from Rs -81 to Rs +539, i.e. flat
either way.

**Results (Rs 100k fixed notional per trade, standard equity cost model,
lagged fills), 12-config perturbation sweep:** all 12 have positive net
P&L and 12/12 pass a 2-way walk-forward split, with a smooth surface (no
cliffs) from Rs 20k to Rs 56k. Taken alone that looks like a pass. It
isn't, because this is a long-only strategy on an ETF that gained 239%
(about 12.9%/yr) over the same window:

- **Time in market is 17-33%**, so the 3.99%/yr base-config figure (on the
  full Rs 100k, idle cash earning 0) is not comparable to the ETF's
  ~12.9%/yr. Per unit of time invested it is roughly 17%/yr (3.99/0.23),
  which looks like it beats the ETF — but that is the raw number the control
  below exists to deflate, since a long-only rule that is in the market only
  during a rising decade earns positive P&L from drift alone.
- **Circular-shift significance control** (new `shift_control()` in the
  probe): rotate the PCR series against the price dates by a random offset
  >= 1 year and rerun the identical rule, 1,500 seeds. This keeps the
  signal's autocorrelation and trade cadence but destroys any real timing
  link, so it measures "what this rule earns on a rising ETF by drift
  alone." Random mean is Rs 15k-21k per config, so drift explains roughly a
  third of the raw P&L by itself. Uncorrected p(random >= actual):
  0.80/26 -> 0.052; 0.85/26 -> 0.051; 0.90/52 -> 0.069; **0.90/26 (base)
  -> 0.084**; 0.90/13 -> 0.123; 0.95/26 -> 0.327. **None clears even an
  uncorrected 0.05.** The best two miss it by 0.001-0.002, which is
  reported as a miss, not rounded into a pass.
- **Decay in the recent quarter**, config-dependent: base config Q4
  Rs 539 (flat), 0.90/52 Rs 39, 0.95/26 Rs -1,544; only 0.85/26 and
  0.90/13 hold Q4 at ~Rs 9k. Base config first-half/second-half Rs 33,027
  / Rs 9,564 — the same shape as most prior rejections here.

Added the 6 p-values above to `multiple_comparisons.py`'s broad family
(now m=16, up from m=10); Bonferroni threshold 0.0031, **0/16 survive**,
BH also 0/16. This doesn't change the standing verdict, it keeps the
running count honest. 3 new unit tests (`tests/test_probe_pcr_signal.py`)
cover the bhavcopy parser, the no-lookahead percentile, and the lagged
fill; full suite 196 passed.

**Net verdict: rejected.** Not because the raw numbers were bad — 12/12
walk-forward-consistent looks better than most prior candidates' raw
numbers — but because (a) the original fill was look-ahead, (b) the
raw P&L is mostly explained by being long a rising ETF part of the time, and (c) once beta drift is controlled for, no config is
distinguishable from random timing. This is the FIRST rejection in this
project caused by a beta-drift confound rather than decay, concentration,
capital-tier walls, or cost drag — worth applying as a standing check to
any future long-only-on-an-index-ETF idea: run the circular-shift control
before reading the walk-forward result. `probe_pcr_signal.py` stays a
probe script; no council review (reserved for genuinely promising
results). Two limits worth naming: PCR here is total-OI over all expiries
(front-expiry-only or OI-change variants weren't tried, and trying them
now would be a fresh search inflating the family), and the series is
weekly, so any faster positioning signal is untested. **The long-only IBS
rotation remains this project's sole standing finding; 56 mechanisms
tested, none has cleared the bar to trade.**


## Fifty-seventh: macro/geopolitical regime probe — analog matching has no out-of-sample skill; a volatility-spike "fear-buy" is the one candidate this entry could not break, on 8 episodes and a control that cannot resolve corrected significance

Requested direction: keep testing new strategies, analyse current market
conditions, find similar global/geopolitical situations in history and say
what repeats and what differs. Because of the Fifty-first entry's
retrofitted-story warning (after a shock it is easy to pick the analog
episodes that "worked"), **three candidates, their thresholds and their
neighbour grids were fixed in `probe_macro_analog.py`'s docstring before any
outcome was looked at**; episodes are found mechanically, never hand-picked.
Data: Yahoo daily, cached in `macro_cache.csv` (NIFTY, India VIX 2008+, S&P,
US VIX, Brent futures, gold, USDINR, DXY, US 10y), plus FRED dated Brent from
1987 for the long-history episode study. Timing: every non-Indian series is
lagged one day (India closes first) and trades fill at the NEXT close after
the decision date (no same-bar fills, the flaw the Fifty-first/Fifty-sixth
entries found). Non-overlapping monthly (21-trading-day) decisions, 2013-2026,
160 of them, standard 0.2%/leg equity cost.

**Current conditions (2026-09-18).** NIFTY 23,346, -11.3% from its 252d high.
Brent futures $103.9 (+36% over 60d, 96th percentile of 2008-2026); FRED
dated Brent $130.8 on 2026-09-15 (+71%/60d), i.e. a ~$25 physical premium to
front futures. USDINR 95.9 (press: record ~96.96 in May; only +1.5% over the
last 60d). US 10y 5.00% (+45bp/60d). India VIX **11.4 (5th percentile)**, US
VIX 15.4, DXY 100.2. Web-sourced context, kept OUT of every test because it
is unverified press/aggregator text: a Middle East supply shock since ~Feb
2026 (Hormuz throughput ~halved, Houthi moves near Bab el-Mandeb, US SPR at
its lowest since 1982), Brent up ~50% since February, FII net selling
~Rs 17,800 cr in September.

**Historical analogs, mechanical definition** (`--episodes`: dated Brent at a
252d high AND +40% over 60 trading days, declustered 170 days; descriptive
only, n is single digits, no p-value claimed). Ten past episodes 1989-2022,
plus this year's onset (2026-03-06, Brent 95.7, VIX 29.5). What REPEATS: S&P
forward 252d return positive in 9 of 10 (only 2022 negative, -10%); Sensex
positive at 252d in 6 of 6 with data (+6% to +77%); but the near term is a
coin flip, S&P 63d positive in only 6 of 10 (1990 -11%, 1999 -7%, 2005 -1%,
2022 -5% were the negatives), Sensex 63d 3 of 6. Oil-shock onsets have
historically been bad for a quarter and fine for a year, with wide dispersion.
What DIFFERS now, checked directly rather than asserted: (1) **fear is
absent** — of the 288 days since 2008 with Brent +30%/60d, India VIX was <=15
on only 8 (2 in 2016, an oil-rebound-from-lows episode, and 6 in 2026 itself);
its median on those days was 23.1 and today's 11.4 is the lowest of any of
them; US VIX at prior onsets was 12-32, median ~21, vs 14.8 now (only 2004
and 2005, demand-driven rises at 10y ~4%, were similarly calm). (2) Rates:
US 10y 5.00% vs <=4.3% at every post-1999 episode; the only comparable rate
levels are 1989-1996, and 2022, the one bad-year outcome, was the fast-rising
-rate one. (3) The dated-vs-futures Brent gap is a physical-tightness signal
no prior row here can be checked for (FRED gives only the dated series). (4)
India is a large net oil importer with the rupee at record lows, a channel
the US-centred 1990-2022 rows do not carry. **The market is pricing this as
a calm one; historically these were not calm.** That is an observation about
the gap between price and history, not a forecast.

**Pre-registered candidates and results** (each tested against the Fifty-sixth
entry's circular-rotation random-timing control, because long-only NIFTY earns
drift for free):

- **A. Analog forecaster** (k=8/15/30 nearest declustered historical states on
  8 features; only past data, only candidates whose forward window had closed).
  First pass looked like a win: k=15 P&L +102% vs random +30%, p=0.007
  (10,000 seeds). **It is a grid-phase artifact**: re-running the identical
  test with the decision grid shifted by 0,3,...,18 days gives p = 0.010,
  0.000, 0.243, 0.450, 0.753, 0.103, 0.390 — significant at 2 of 7 phases,
  median ~0.24. Feature ablation (k=15) says which inputs matter: geopolitical/
  macro features only (Brent, USDINR, US10y, DXY) **anti-predict** (rank-IC
  -0.133, p=0.914); volatility only p=0.078; everything except volatility
  p=0.090; NIFTY-state only p=0.164. **Matching today to historical
  geopolitical/macro states has no demonstrable skill on NIFTY 2013-2026;
  whatever the full model saw came from the volatility inputs** — i.e. from
  candidate C in disguise.
- **B. Oil+rupee stress filter** (flat NIFTY while Brent 60d >= B and USDINR
  60d >= I; 2x2 grid). Stress fires in only 2-8 of 160 months; p = 0.188,
  0.339, 0.058, 0.339. Mean forward 21d NIFTY in the 3 months of the tightest
  active cell was -2.7% vs +1.1% calm — directionally what one would expect
  and statistically untestable at n=3. **Inconclusive, not a finding.**
- **C. Fear-buy** (long NIFTY for 21 days when VIX / its trailing-252d median
  >= X; India VIX and US VIX, X in {1.4, 1.5, 1.7}). India VIX: 10-14 of 160
  months on, mean forward 21d **+7.4%** in the triggered months (median
  +6.0%, minimum +2.2%, 12 of 12 positive) vs +1.0% unconditional (61% of
  all months up); raw P&L +73% to +86% vs random-timing mean ~+7-10%.
  Rotation-control p ≤ 0.017 at all 7 grid phases (A's failure mode does NOT
  recur). The 12 months are 8 independent episodes (2013, 2014, 2015, 2019,
  2020 [4 consecutive months], 2022, 2024, 2026), positive in every calendar
  year that has one; without the best 3 months the sum is still +48%. Quarter
  split (India VIX >=1.5): +24.2% / +6.0% / +50.3% / +8.4% — all positive,
  most recent quarter positive, no decay (that quarter includes this year's
  2026-04-02 trigger, +5.9%). US VIX is weaker: p phase-dependent (0.000 to
  0.483), 4 negative months, quarters as low as +2.0%.

**A defect in my own control, found and disclosed.** The rotation control
prints "p=0.000" for C even at 10,000 seeds. It cannot be: 160 monthly
decisions rotated by >=12 give only ~136 distinct offsets, so extra seeds just
resample them and the smallest reportable p is ~1/137 = 0.0073. This same
floor applies to the Fifty-sixth entry's PCR test to a lesser degree (~450
offsets, floor ~0.002; none of its p-values were near it). Added a second
null with an unbounded state space (`subset_p`: draw the same NUMBER of
"on" months uniformly at random, (count+1)/(n+1) convention): India VIX
p = 0.00005 at all three thresholds (the 20,000-draw floor; no draw reached
the observed mean), US VIX 0.0107 / 0.0032 / 0.0002. The random-subset null is
KINDER (it ignores that triggers cluster in time); the rotation null is
stricter but cannot resolve anything below 0.0073. The truth is between them.

**Adversarial self-review (per the hook's rule for a promising result; no
fresh-judge council was run for this entry).**
1. *It is partly "buy a deep drawdown."* A drawdown-only trigger with no VIX
   (NIFTY <= -12.5% from its 252d high, 13 months) earns +5.35% mean fwd 21d
   vs +7.40% for the India VIX trigger. The VIX adds roughly +2pp, and two
   triggered months (2014-04, 2024-05, both election-driven VIX spikes with
   NIFTY <1% off its high, +9.0% and +2.5%) fired with no drawdown at all,
   but the drawdown explanation is real and untested for significance.
2. *8 independent episodes.* All positive, but 8 is not a large number and
   one (March 2020) contributes +22% in a single month; a "buy panic" rule
   is exactly the shape that gets destroyed by the one episode that keeps
   falling, and there was none in the window.
3. *Multiple comparisons.* 29 p-values now sit in `multiple_comparisons.py`'s
   broad family. **Bonferroni (threshold 0.00172): nothing passes under the
   rotation null** (best registered value 0.0070, and C is registered at its
   0.0073 resolution floor, the conservative reading). Under the random-subset
   null India VIX (5e-5) clears 0.00172 by 30x. The candidate passes a
   corrected bar only under the kinder null. BH flips IBS rotation top_k=5/8
   to "PASS" at m=29 — an artifact of adding near-identical and floor-valued
   rows (documented in a comment above the registry); **IBS's retired
   significance claim (Fifty-third entry) is NOT reinstated by that.**
4. *Execution.* Buying a panic close is when spreads are widest. Adding 0.5%
   extra slippage per leg cuts the triggered-month sum from +88.8% to +76.8%
   (arithmetic: 12 x 1.0%); the effect is not slippage-fragile. The symmetric/
   asymmetric-slippage caveat from the Fifty-second entry applies unchanged.
5. *Frequency is the practical problem.* ~1 trigger per year (12 in 13.7
   years), 7.5% time in the market. Raw sum +85.7% over 13.7 years on the
   capital deployed is ~6.5%/yr with idle cash earning nothing, below
   NIFTYBEES buy-and-hold (~12.9%/yr); the case for it is as a low-exposure
   timing overlay or an add-on entry, not a standalone strategy.
6. *Prior.* "Buy volatility spikes" is a long-documented US effect, not a
   pattern mined from this data, and its thresholds were set before looking,
   which is what separates it from the many rejected single-instrument
   survivors here. It is also exactly the bet the Cross-mechanism synthesis
   and the Fifty-first entry's council warned about: a mean-reversion bet
   that has paid in a regime with repeated V-shaped recoveries.

**Current status of C:** India VIX / 252d median = 0.93 and US VIX 0.90
(trigger is >= 1.5). **The trigger is OFF today**, despite Brent +36%, which
is itself the entry's central observation: the market has not priced fear
into this shock, so the one signal this entry found predictive is silent.
`python probe_macro_analog.py --state-only` prints it.

**Net verdict.** Analog matching against geopolitical/macro history: no
skill (A, phase-fragile, ablation says the geopolitical features carry none).
Oil+rupee stress filter: too few trigger months to test (B). VIX-spike
fear-buy: survived every check this entry could construct, including three
that failed A, but on 8 episodes, one control that cannot resolve corrected
significance, a competing drawdown explanation, and a regime of V-shaped
recoveries. Not declared tradable. 200 tests pass (4 new in
`tests/test_probe_macro_analog.py`, including a leak test that a
not-yet-closed forward window is unreachable by the analog matcher and that a
same-day non-Indian shock cannot reach the features). **Concrete next steps,
none done here:** (1) a forward trigger log for C (append-only, fires ~1x/year,
so the record accumulates slowly); (2) test whether IBS rotation's own P&L
differs in the months after a VIX spike (the two are both mean-reversion
bets, likely correlated, which would mean C adds less than it appears);
(3) significance-test the drawdown-only rival before crediting VIX with the
extra 2pp. **The long-only IBS rotation remains this project's sole standing
finding; 57 mechanisms tested, none has cleared the bar to trade.**


## Fifty-eighth: following up the VIX-spike fear-buy — the Fifty-seventh entry's headline (12/12 months, +7.4%) was partly the decision grid's luck, the "wait for persistence" fix does not replicate on the S&P 500, and IBS rotation is not this trigger in disguise

Direct follow-up on the Fifty-seventh entry's own three "next steps"
(`probe_fear_followup.py`), plus two checks that entry's grid-phase lesson
called for. **Net effect: the fear-buy is downgraded from "the one candidate
this entry could not break" to "a weak, market- and sample-specific effect."**

**1. Grid-free re-test.** Instead of the 160 non-overlapping monthly decision
days (whose sampled days happen to fall anywhere inside a spike), take every
day the trigger is on and keep one entry per 21-trading-day spike cluster
(the FIRST day, the real-time-executable definition). India VIX / 252d median:
>=1.5 gives **23 events, not 12**; mean forward 21d **+2.38%** (not +7.4%),
median +2.77%, **16/23 positive, worst -28.5%**, random-day p=0.063
(unconditional mean +0.91%, 61% of days up). >=1.4: 30 events, +1.66%, p=0.194;
>=1.7: 13 events, +3.35%, 9/13, p=0.026. US VIX version: +1.1% to +1.4%,
p=0.26-0.43. The Fifty-seventh entry's "p=0.000" and "12 of 12" describe the
monthly-grid version only; no honest reading of the real-time trigger supports
them. (Returns are gross of the 0.4% round-trip cost.)

**2. Drawdown rival: not separable in the event version, partly separable in
the regression.** A NIFTY-drawdown-only trigger matched on count (<= -15% from
the 252d high, 22 events) earns +2.36% (p=0.070) vs the India VIX trigger's
+2.38% (p=0.063), and the two share only 1 entry day, i.e. they are two
different, equally weak signals. Daily OLS of forward 21d return on both
(standardised, Newey-West 21-lag t-stats): VIX-rel alone beta +0.0088, t=4.56;
drawdown alone t=-2.99; together VIX-rel keeps t=+2.18 and drawdown falls to
t=-1.32. So VIX carries some information beyond the drawdown level, but the
per-event edge is small.

**3. IBS rotation overlap: it is NOT the same trade.** The 52-stock monthly IBS
rotation's return in the 12 months right after an India-VIX spike (rel>=1.4 at
the month-end rebalance) averages +2.44% vs +1.84% in the other 108 months
(random-month p=0.38); removing the spike months leaves +504% compounded vs
+632% with them. The IBS finding does not depend on VIX spikes. The overlap
also carries a risk note: its single worst month in 10 years, 2020-02-28
(-29.9%), was a spike month, followed by +25.9% and -2.3% — the
bounce-harvesting volatility the Fortieth/Fifty-first entries flagged
concentrates exactly where a fear-buy would also be active, so the two are
not independent diversifiers in a crash.

**4. Why the grid version looked so much better (exploratory, post hoc, no
significance claimed).** Forward 21d return by how long the spike has been
on: first day +1.3% (n=35), days 1-5 +3.5% (74), days 6-15 **+6.7%** (68), day
16+ +5.8% (96). Entering k days after the first crossing (23 events): k=0
+2.38% (16/23, worst -28.5%), k=5 +3.34% (19/23), **k=10 +5.05% (20/23, worst
-2.4%)**, k=15 +3.54% (18/23). A monthly grid samples mostly mid-spike days,
which is why it looked like +7.4%: the effect was a "wait for persistence"
effect that the fixed grid happened to encode, not a property of spikes
per se. That is a hypothesis generated from the same 23 events it fits (5
delays tried), so it needed out-of-sample support.

**5. Out-of-sample replication on the S&P 500, 1990-2026 (74 first-crossing
events, VIX / 252d median >= 1.5; includes 2000-02 and 2008, which the India
sample — India VIX rel needs 252 days of a series starting 2008-03, so 2009+ —
structurally cannot contain).** Unconditional mean fwd21 +0.81% (64% up).
Enter +0d: +1.21%, 46/74, worst -22.0%, p=0.222 (first half +0.02%, second
half +2.40%). +5d: +1.25%, p=0.200. **+10d: +1.38%, 51/74, worst -18.6%,
p=0.136.** +15d: +1.61%, p=0.060. The four worst +10d events are all 2008
(-18.6%, -13.1%, -12.4%, -10.0%). **The persistence effect does not
replicate:** a delay improves the US numbers only marginally and none clears
0.05 uncorrected. The India +5% at k=10 came from a 2009+ sample of V-shaped
recoveries with no 2008 in it — the exact regime-fluke risk the Fifty-first
entry's council named for any mean-reversion bet that has paid in this window.

**Multiple comparisons.** 10 more p-values registered (6 grid-free, 4 S&P
delays), family m=39, Bonferroni threshold 0.00128; nothing passes; the six
rotation-floor rows from the Fifty-seventh entry stay in the registry for
counting honesty but are superseded by these. The BH "flip" of IBS rotation
noted in that entry vanishes at m=39, confirming it was an artifact of the
floor-valued rows.

**Net verdict.** The VIX-spike fear-buy is not a finding this project can
lean on: in real-time (first-crossing) form it is +2.4% per 21 days at
p=0.06 on India (16/23 positive, one -28.5% loss), +1.2% at p=0.22 on the
S&P since 1990, statistically indistinguishable from a drawdown-only rule,
and its persistence refinement fails out of sample. What survives from the
Fifty-seventh entry is the descriptive observation (India VIX is at its 5th
percentile during a Brent +36% shock, unlike every prior such episode since
2008), not a strategy. The trigger is still OFF (0.93). The Fifty-seventh
entry's "concrete next step (1)", an append-only forward trigger log, is
therefore NOT worth building. **The long-only IBS rotation remains this
project's sole standing finding; 57 mechanisms tested, none has cleared the
bar to trade.** (This entry adds no new mechanism count: it rejects its own
predecessor's follow-ups.) 203 tests pass (3 new).


## Fifty-ninth: closing the IBS backtest's same-bar-fill look-ahead (costs ~2 points a year, IBS survives), and testing the classic short-term reversal factor — a smooth-looking pass that survivorship stress destroys

Two tasks in one probe (`probe_reversal_rotation.py`, a numpy re-implementation
of the monthly rotation so a fill LAG can be applied identically to a strategy
and to its random-portfolio control). It reproduces the family's existing IBS
number before being trusted: lag 0, top_k=5, 52 stocks, 10y gives 22.29%/yr and
38.2% max drawdown vs the Thirty-ninth/Fifty-fourth entries' 22.1-22.2% and
38.2%.

**Part A — the Fifty-first entry's still-open backtest look-ahead.** Every
rotation backtest since the Thirty-eighth ranks on the month-end close and fills
at that SAME close, which no real account can do (only the live trackers were
fixed, in the Forty-ninth entry). `lag=1` fills at the first close after the
ranking date. IBS(5) rotation, 52-stock WIDE_UNIVERSE, 1,500-seed lag-matched
random control (random mean ~13.2-13.9%/yr either way):

| top_k | lag 0 | lag 1 | p (lag 0 -> lag 1) |
|---|---|---|---|
| 3 | 22.84%/yr, DD 38.0% | 20.81%/yr, DD 39.7% | 0.044 -> 0.055 |
| 5 | 22.29%/yr, DD 38.2% | 20.05%/yr, DD 39.5% | 0.015 -> 0.051 |
| 8 | 21.03%/yr, DD 31.6% | 19.11%/yr, DD 33.3% | 0.009 -> 0.030 |

The look-ahead was worth ~2.0-2.2 points of annual return and ~1-2 points of
drawdown; both walk-forward halves and all four quarters stay positive at every
size. It also pushes the uncorrected p-value at the project's standard top_k=5
from 0.015 to 0.051 — the significance claim the Fifty-third entry already
retired is now not even a raw p<0.05 once filled realistically. **The backtest
numbers cited for IBS rotation (22%/yr) are best read as ~20%/yr going
forward.** The two live paper trackers already use next-settled-bar data and
are unaffected.

**Part B — short-term reversal (Jegadeesh 1990 / Lehmann 1990), a real
academic factor never tested directly here** (IBS and RSI-2 are proxies for
it). Score = trailing N-day return, long the top_k biggest losers, monthly,
same cost model, lag 1. Pre-registered grid: N in {5, 10, 21} x top_k in
{3, 5, 8}, walk-forward halves + quarter-split + 1,500-seed lag-matched control.
Full-period results (all 9 cells positive, all 4 quarters positive in 8 of 9):
rev(5): 14.8/16.7/13.6%/yr, p 0.35/0.17/0.40; rev(10): 20.6/15.8/17.9%/yr,
p 0.071/0.239/0.056; **rev(21): 14.8/19.0/20.9%/yr, p 0.326/0.066/0.0027**.
The single standout, rev(21) top_k=8, has 20.93%/yr, 39.0% DD, halves
+174%/+143%, quarters +54/+80/+58/+51%, and its p=0.0027 clears the
9-cell within-grid Bonferroni threshold (0.0056). Everything else does not.
The surface is smooth (returns and p-values improve monotonically with both
window and portfolio size) but the best cell sits at the CORNER of the grid,
the usual warning that the peak may lie outside it. An exploratory extension
(post hoc, all registered in the multiple-comparisons family) finds a
plateau, not a taller peak: rev(21) top_k=12 18.2%/yr p=0.016; rev(42) top_k=8
19.0% p=0.053; rev(63) top_k=8 18.0% p=0.090; rev(42) top_k=12 18.4% p=0.023.

**Three checks, each of which the candidate fails:**
1. *Survivorship stress* (the Fortieth entry's four real blowups, JETAIRWAYS/
   YESBANK/RCOM/PCJEWELLER, added to the universe: 56 stocks). A reversal
   strategy is the most exposed to survivorship bias there is (its universe of
   "biggest losers" is missing every loser that kept falling to delisting).
   rev(21) top_k=8 falls from 20.9%/yr to **15.6%/yr with a 57.3% drawdown and
   p=0.112**; the other reversal cells collapse: rev(21) top_k=3/5 3.0%/7.4%
   with 87.8%/76.1% drawdowns, rev(10) 1.0-7.1%/yr with 72-84% drawdowns and
   p 0.79-0.94. **IBS(5) rotation, under the identical stress and lag,
   IMPROVES** (21.87%/yr at top_k=5, p=0.015; 22.67%/yr at top_k=8, p=0.0027,
   DD 33.5%): the "closed near today's low" signal harvests bounces without
   buying cumulative losers into a collapse (the Fortieth entry's
   bounce-harvest concern still applies to IBS, this just shows it is a
   different, less fragile exposure than raw reversal).
2. *Overlap with IBS.* Monthly-return correlation of rev(21) and IBS(5)
   (top_k=8, lag 1) is **0.85** (0.83 under stress) although only ~31% of the
   picks coincide; a 50/50 blend of the two return streams earns 20.2%/yr vs
   rev 20.9% / IBS 19.1% — no diversification. Reversal is largely the same
   short-term-reversal effect IBS already captures, not an independent one.
3. *Multiple comparisons.* 16 more p-values registered (3 IBS lag-1, 9 grid,
   4 post-hoc), family m=55, Bonferroni threshold 0.00091: rev(21) top_k=8
   (p=0.0027) fails; nothing passes under either Bonferroni or BH.

**Net verdict: rev(21) rotation rejected.** A real, published factor that
reproduces in backtest and beats a random-portfolio control on the survivors-only
universe, but its edge is largely IBS again (0.85 correlated) and disappears
under the one bias reversal strategies are most vulnerable to, where IBS is
unaffected. **IBS rotation remains the sole standing finding, now with a more
honest expected return (~20%/yr, not 22%), still no surviving significance
claim, and still not declared tradable.** New standing check for any future
loser-buying/oversold-ranking candidate: run the four-blowup survivorship stress
BEFORE reading its walk-forward or p-value; `--stress` in this probe does it.
58 mechanisms tested; none has cleared the bar to trade. 205 tests pass (2 new,
covering the fill-lag logic that is the point of this file).


## Sixtieth: measuring the asymmetric-slippage risk the Fifty-first/Fifty-second entries left open — IBS's picks do NOT face materially wider spreads than a random stock, by the one proxy this project's data supports

The Fifty-second entry showed IBS rotation is robust to a SYMMETRIC slippage
haircut but stated that such a test cannot see the real worry: that stocks which
just closed near their low (often after a large one-day move) fill worse than
average. That was left "unresolved, no real bid-ask data available." This entry
measures it as well as daily OHLC allows, instead of leaving it as prose.

**Method** (`probe_reversal_rotation.py --spread`). Corwin & Schultz (2012)
high-low spread estimator per stock-day (two-day window ending on the fill day,
negatives floored at 0, 3-day smoothed, halved to a half spread), applied as a
per-pick cost on both legs of the lag-1 rotation. Each pick pays ITS OWN
estimated half spread, for the strategy and for every random-control draw alike,
so if the strategy's picks really sit in wider-spread situations the
penalty falls on them and not the control — the asymmetry is priced in by
construction, not assumed. Same 52-stock universe, 10y, 1,500 seeds.

**Known limitation, stated first:** the estimator is biased UP on high-volatility
days (a wide range from volatility is misread as spread), and IBS picks are
exactly the volatile-day stocks, so this is an upper-leaning read of the
asymmetry. Its ABSOLUTE level is not credible either: the universe-mean half
spread comes out at 0.334%, roughly 5-10x what liquid Nifty stocks trade at.
Treat the x1 rows below as a severe stress, x2 as absurd, and the RATIO as the
finding.

**Results (top_k=5, lag 1).**
- IBS(5): mean half spread paid on entry **0.364% for the strategy's picks vs
  0.352% for random picks, a ratio of 1.03x**. No spread cost: 20.05%/yr. Own
  half spread on both legs, x1: 9.82%/yr (DD 40.6%) vs random control
  4.20%/yr, p=0.058; x2: 0.32%/yr vs control -4.64%/yr, p=0.065.
- rev(21): picks 0.372% vs random 0.350%, ratio 1.06x; x1 8.84%/yr vs control
  4.08%, p=0.079; x2 -0.59%/yr vs -4.65%, p=0.101.

**Read:** the asymmetric-slippage risk the council raised, measured by a proxy
biased toward finding it, is small: picks are 3% (IBS) to 6% (reversal) wider
than random, not the multiples feared. The much larger effect is simply the
absolute cost level, and that is a property of the proxy's overstatement. Even
under the inflated x1 cost, IBS keeps a ~5.6-point annual edge over an equally
burdened random portfolio (9.8% vs 4.2%). What this does NOT settle: real
execution on the fill day (queue position, impact of buying into a falling
stock at the close, circuit-limit days) cannot be inferred from a high-low
range. The paper trackers still run zero slippage; the number this entry
supports is "expected fill drag is likely well under 0.1% per leg for Nifty
names, and no evidence it is disproportionately worse for the picks."

**Multiple comparisons.** 4 more p-values registered (m=59); none clears
Bonferroni (0.00085) and IBS's p-values sit at 0.058/0.065 with the inflated
cost, consistent with Part A of the Fifty-ninth entry (IBS's raw p is ~0.05 at
lag 1 and no longer claimed as significant). 207 tests pass (2 new).

**Net verdict:** closes the last concretely-flagged execution-realism gap for
IBS rotation (slippage magnitude: Fifty-second; slippage asymmetry: here, by the
best available proxy; whole shares: Fifty-fourth; fill lag: Fifty-ninth).
**IBS rotation remains the sole standing finding at ~20%/yr backtest, with
no surviving significance claim, unresolved survivorship exposure, and only
2 days of forward paper data; not declared tradable. 58 mechanisms tested.**


## Sixty-first: a NIFTY trend gate on IBS rotation — the first drawdown control that roughly doubles Calmar instead of just trading return for risk, robust at top_k=5 but not at top_k=8

IBS rotation's real weakness is a ~39% max drawdown (Fifty-ninth entry, lag-1
fills). Prior attempts to cut it — a per-stock short leg (Forty-fourth), a NIFTY
futures hedge (Forty-fifth, capital-blocked), a half NIFTYBEES hedge
(Forty-sixth/Forty-seventh, 12.0%/yr at 24.2% DD, i.e. Calmar 0.50 vs the
unhedged 0.58) — either destroyed the edge or did not improve risk-adjusted
return. This entry tests the standard alternative, a whole-portfolio trend
gate: at each month-end ranking close, if NIFTY is below its L-day simple
moving average, hold cash (0%, no cost) for the next month, else run the
normal lag-1 rotation. The signal is known at the ranking close, before the
next-close fill (no new look-ahead). Pre-registered before running:
L in {100, 150, 200} x top_k in {5, 8}. (`probe_reversal_rotation.py --gate`.)

**52-stock WIDE_UNIVERSE, 10y (ungated: top_k=5 20.05%/yr, DD 39.5%, Calmar
0.51; top_k=8 19.11%/yr, DD 33.3%, Calmar 0.57).** The gate puts the portfolio
in cash 38-40 of 120 months (a third of the time; cash earns 0 here).

| top_k / SMA | return/yr | max DD | Calmar | worst month | p(DD as low as random off-months) |
|---|---|---|---|---|---|
| 5 / 100 | 15.11% | 13.5% | 1.12 | -12.4% | 0.040 |
| 5 / 150 | 14.79% | 14.8% | 1.00 | -12.4% | 0.062 |
| 5 / 200 | 13.97% | 17.9% | 0.78 | -12.4% | 0.116 |
| 8 / 100 | 13.58% | 11.4% | 1.19 | -10.5% | 0.016 |
| 8 / 150 | 12.48% | 16.2% | 0.77 | -10.6% | 0.100 |
| 8 / 200 | 10.75% | 17.9% | 0.60 | -10.6% | 0.153 |

(Ungated worst month: -30.0% at top_k=5, -24.7% at top_k=8.) Both walk-forward
halves and all four quarters are positive in every cell. The gate costs 5-9
points of annual return (13.4-15.1% vs 20.05% at top_k=5) but takes drawdown
down by 20-26 points; and the return it keeps is NOT distinguishable from
turning the same number of RANDOM months off (p(return) 0.28-0.71: it does not
"time" returns better than chance), while the drawdown it avoids IS better than
random off-months at the shorter SMAs (p 0.016-0.062 at SMA100/150, top_k=5/8;
0.116-0.153 at SMA200). Smooth across L: return falls and drawdown rises
monotonically from SMA100 to SMA200, no cliff. It beats the half-hedge in
risk-adjusted terms (Calmar 1.0-1.1 vs 0.50) over the same window.

**Survivorship stress (the Fortieth entry's four blowups added, 56 stocks;
ungated top_k=5 21.87%/yr DD 36.2%, top_k=8 22.67%/yr DD 33.5%).** top_k=5
holds up: SMA100/150/200 give 19.13/19.75/18.48%/yr at DD 20.8/14.8/15.2%,
Calmar 0.92/1.34/1.22 vs 0.60 ungated, halves and quarters all positive. **top_k=8
does not:** SMA100 helps (16.34%/yr, DD 18.9%, Calmar 0.86 vs 0.68) but SMA150
and SMA200 leave DD at 27.9%/30.6% with Calmar 0.57/0.45, i.e. worse than
ungated, and p(DD) 0.40-0.46. So the benefit is robust only for the concentrated
top_k=5 book, where a cluster of blowup-name picks is what the gate dodges;
the broader top_k=8 book already diversifies away part of the tail the gate
targets, and the gate then mostly just costs return.

**Caveats that matter.**
1. *Drawdown is measured on month-end capital points only* (the whole rotation
   family's convention). Intramonth drawdowns are larger and a monthly gate
   cannot act on them, so the true improvement is smaller than the tables show.
2. *One regime.* 2016-2026 contains one crash (March 2020) and otherwise a
   rising market; a trend gate's classic failure mode is whipsaw in a
   sideways year, and there is no such stretch of length here.
3. *Selection.* p(DD) values are 0.016-0.153 uncorrected; six of them are
   registered in `multiple_comparisons.py` (m=65; Bonferroni threshold 0.00077);
   none clears it, so this is descriptive, not confirmed. The return side of
   the gate has no statistical support at all.
4. *Cost.* A third of the time in cash is a real opportunity cost (cash yield
   not modeled; at ~6% it would add roughly 2 points/yr).
5. *Prior.* Trend filters are a standard, prior-motivated overlay, not a
   pattern mined from this data, and the L grid was fixed in advance.

**Net verdict.** Not a new mechanism (no count change), but the most useful
result for IBS rotation's actual bottleneck since the ETF hedge: at top_k=5, a
100-150-day NIFTY trend gate roughly doubles Calmar (0.51 -> 1.0-1.1; 0.60 ->
0.9-1.3 under blowup stress) at a cost of ~5 points/yr and a third of the time
in cash. It does not fix top_k=8, does not turn the return significant, and
its edge is one crash. **Still not declared tradable.** Concrete next steps:
(1) NO third tracker is needed — the gated forward record is fully determined by
the existing unhedged log (`paper_track_ibs_rotation_log.json`) plus NIFTY's own
closes, since the gate is only a cash override on that log's dated picks; each
month, read the gate off `python probe_macro_analog.py --state-only` (prints
NIFTY vs SMA100/150/200 -> RISK-ON/OFF) and treat a RISK-OFF month's logged
picks as cash when evaluating the gated variant. How often the gate flips and
whether it whipsaws is exactly what a one-crash backtest cannot answer, and
that is what the forward record will show; (2) check the gate
against a longer NIFTY history with the SAME rule applied to the IBS signal on
older data if a point-in-time universe ever becomes available, (3) test a
cash-yield-adjusted version. **The long-only IBS rotation remains the sole
standing finding; 58 mechanisms tested, none has cleared the bar to trade.**


## Sixty-second: the trend gate on the index alone — the drawdown effect replicates over 70 years of S&P 500 and through the 2008 crash on NIFTY, so it is a generic trend-filter property, not something IBS-specific

The Sixty-first entry's gate had exactly one crash in its sample (March 2020).
This entry applies the identical rule to the INDEX with no stock selection at
all — hold the index next month iff its close is above its L-day SMA at the
month-end (fill at the next close, 0.2% on each switch, month-end drawdown,
random-off-months control with the same number of cash months, 2,000 draws) —
on two longer histories: NIFTY 2008+ (227 months, includes the 2008 crash) and
the S&P 500 1950+ (919 months). (`probe_reversal_rotation.py --index-gate`.)

| market | SMA | cash months | switches | return/yr | max DD | Calmar | p(DD vs random) | p(return vs random) |
|---|---|---|---|---|---|---|---|---|
| NIFTY 2008+ | buy&hold | - | - | 8.58% | 56.5% | 0.15 | - | - |
| | 100 | 80/227 | 50 | 7.13% | 30.1% | 0.24 | 0.070 | 0.260 |
| | 150 | 70/227 | 39 | 7.03% | 23.7% | 0.30 | 0.011 | 0.323 |
| | 200 | 62/227 | 35 | 5.74% | 36.6% | 0.16 | 0.177 | 0.566 |
| S&P 500 1950+ | buy&hold | - | - | 8.30% | 54.7% | 0.15 | - | - |
| | 100 | 298/919 | 220 | 4.95% | 26.0% | 0.19 | 0.0005 | 0.760 |
| | 150 | 276/919 | 144 | 6.54% | 23.3% | 0.28 | 0.0005 | 0.164 |
| | 200 | 260/919 | 110 | 7.34% | 27.1% | 0.27 | 0.0005 | 0.038 |

**What replicates:** on the S&P the gate halves month-end drawdown (54.7% ->
23-27%) at all three lengths and roughly doubles Calmar (0.15 -> 0.27-0.28
for SMA150/200), at a cost of 1-3 points a year for SMA150/200 and 3.4 for
SMA100, with the drawdown reduction beating random cash months at the
2,000-draw resolution floor (p=0.0005, which clears the m=71 Bonferroni
threshold 0.0007) — a real, corrected-significance result, on a well-known
effect (Faber 2007) replicated on 70 years of data. On NIFTY, through 2008,
drawdown falls 56.5% -> 23.7-36.6% and Calmar rises at SMA100/150. Together with
the Sixty-first entry (Calmar roughly doubling for IBS rotation), the gate is a
consistent halving of drawdown across three independent samples.
**What does not:** the best length differs by market (S&P: SMA200 keeps the most
return and p(return)=0.038 is the only return-side result under 0.05; NIFTY:
SMA150 is best and SMA200 is the WORST, DD 36.6%; IBS rotation: SMA100 best),
so no single L should be tuned; 110-220 switches over the S&P sample show real
whipsaw cost at SMA100; and the gate does not "time returns" better than
random months (return p-values mostly 0.16-0.76): it reduces risk, it does not
add alpha.

**Implication for the standing candidate.** A plain gated NIFTY/S&P index
position earns ~7%/yr at Calmar 0.27-0.30; a gated IBS rotation (Sixty-first
entry) earns ~14-15%/yr at Calmar 1.0-1.1 (one-crash, 2016-2026 sample). So
the gate is not what makes IBS rotation good — it is an inexpensive
drawdown overlay that is now independently corroborated, applied to the same
underlying edge. **No count change; IBS rotation remains the sole standing
finding; 58 mechanisms tested; not declared tradable.** Today's gate reading
(2026-09-18): NIFTY 23,346 is below its SMA100/150/200 (23,952/24,051/24,494),
RISK-OFF on all three, meaning the gated variant would be in cash for the
month the two live trackers logged picks; the unhedged tracker's 2026-09-17
record is unaffected (logs are never edited).


## Sixty-third: cross-sectional 12-1 momentum and 52-week-high rotation, re-tested properly — worse than a random portfolio, not just "decayed"

Closes an evidence gap in the Cross-mechanism synthesis: the basket-wide
directional bets it says decay recently (Fourth entry's 6-1 momentum rotation,
Thirty-fifth/Thirty-sixth single-instrument 52-week high) were tested BEFORE this
project had a fill lag or a lag-matched random-portfolio control. Both classic
academic signals were re-run in the Fifty-ninth entry's machinery (52 stocks,
10y, monthly, lag-1 fill, same costs, 1,500-seed control, top_k 3/5/8):
Jegadeesh-Titman 12-1 momentum (return from t-252 to t-21) and George-Hwang
nearness to the 252-day high (`probe_reversal_rotation.py --momentum`).
Annualised over the months actually invested (each needs ~1y of history).

| signal | top_k | return/yr | max DD | halves | quarters | random mean | P(random >= actual) |
|---|---|---|---|---|---|---|---|
| 12-1 momentum | 3 | 2.45% | 54.7% | +76%/-29% | +16/+55/+13/-39% | 13.00% | 0.954 |
| | 5 | 7.96% | 45.3% | +61%/+23% | +20/+43/+44/-20% | 12.65% | 0.820 |
| | 8 | 6.58% | 39.4% | +64%/+8% | +13/+53/+33/-23% | 12.24% | 0.937 |
| 52-wk high | 3 | -1.08% | 46.7% | +25%/-27% | -22/+68/-13/-21% | 13.00% | 0.990 |
| | 5 | 3.82% | 36.5% | +53%/-9% | +2/+56/+2/-14% | 12.65% | 0.973 |
| | 8 | 2.96% | 38.6% | +28%/+1% | -15/+59/+13/-15% | 12.24% | 0.997 |

**Every cell underperforms a random portfolio** (a P(random >= actual) near 1
is the wrong side of the test: the strategy loses to most random draws), the
most recent quarter is negative in all six, and the second walk-forward half is
weak or negative in five. Adding the Fortieth entry's four blowups (56 stocks)
changes nothing (2-5% return/yr, same signs, P 0.85-0.98). One reading of the
mirror image: 52-week-high top_k=8 sits at the 0.3rd percentile of random
portfolios, i.e. in this universe/period "near the high" was a reliably WORSE
place to buy than a coin-flip stock — consistent with the mean-reversion
signals (IBS, reversal) working; not exploitable as a short leg (the
Forty-fourth entry's SLB caveat, and that entry already showed shorting
IBS's mirror image lost).

**Net verdict: two more rejections, and a stronger basis for the Cross-mechanism
synthesis** — basket-wide momentum is not just "decayed in Q4" but underperforms
random selection over the full 2016-2026 window in this universe. 6 p-values
registered (m=77); they are on the wrong side and change no verdict. 209 tests
pass (1 new: momentum score direction). **58 -> 60 mechanisms tested (12-1 and
52-week-high cross-sectional rotations are new constructions; 6-1 momentum was
the Fourth entry). IBS rotation remains the sole standing finding; not declared
tradable.**


## Sixty-fourth: the IBS rotation edge is concentrated in month-end entries — not in "buying oversold stocks" in general; the first paper-tracker record was logged in the backtest's worst phase

Prompted by an anomaly, not a plan: the Fifty-ninth entry's rebalance-frequency
sweep returned 7.6%/yr, p=0.92 for "IBS(5), every 21 trading days" against
20.1%/yr, p=0.05 for the calendar month-end grid — two nominally monthly
schedules of the SAME signal, costs and fills. That is exactly the grid-phase
fragility that sank the Fifty-seventh entry's candidate A, applied to the
standing finding. Investigated in two steps (`probe_reversal_rotation.py
--phase`, `--anchor`; lag-1 fills, top_k=5, 52 stocks, 10y, lag-matched random
control, 400-600 seeds per cell; validation against the known 20.1%/yr).

**1. Fixed 21-day step, all 21 phase offsets** (drifts against the calendar,
since months are 21-23 trading days): IBS(5) averages **15.2%/yr** (median 15.9,
range 7.6-21.3) against a random-portfolio mean of ~14.0%: an average edge of
~1.2 points, with **0 of 21 phases at p<0.05** (best 0.055). Reversal rev(21)
top_k=8: mean 17.4%/yr, 7/21 phases at p<0.05. The calendar month-end grid
(20.2%, p=0.048) sits at the very top of the IBS phase distribution.

**2. Calendar-anchored: rebalance on the j-th trading day before each
month-end, j=0..20** (j=0 is the month-end used everywhere else; no drift):

| IBS(5) top_k=5 | j | return/yr | p vs random |
|---|---|---|---|
| last 5 trading days | 0,1,2,3,4 | 20.2, 18.9, 22.5, **24.4**, 21.3 | 0.048, 0.050, 0.010, **0.003**, 0.032 |
| next week | 5,6 | 17.7, 18.3 | 0.146, 0.093 |
| mid-month | 7,8,9 | 12.0, 9.8, **7.0** | 0.62, 0.78, 0.90 |
| | 10,11 | 13.8, 14.4 | 0.37, 0.33 |
| ~day 8-10 of the month | 12,13 | 22.0, 22.3 | 0.022, 0.018 |
| | 14,15,16,17,18 | 14.6, 6.9, 8.9, **4.9**, 6.3 | 0.31-0.97 |
| start of month | 19,20 | 14.1, 20.7 | 0.39, 0.050 |

Across the 21 anchors the mean is 15.3%/yr (median 14.6, range 4.9-24.4) vs
~13.5% for random; 8 of 21 anchors reach p<0.05 (adjacent anchors overlap
heavily, so these are far from 8 independent confirmations). rev(21) top_k=8 is
similar but wider: j=0..8 are 17-21%/yr (p 0.003-0.09), j=9..14 fall to 10-15%
(p 0.35-0.90). Average of the last-5-days anchors (j=0..4) is 21.4%/yr vs 13.3%
for the rest.

**What survives a first check** (post hoc: the "last 5 trading days" window was
defined AFTER seeing this scan, so treat everything below as a hypothesis):
mean monthly return of the last-5-days anchors vs the mid/late-month anchors
(j=7-9, 15-18): first half +2.03% vs +1.22% (diff +0.81%), second half +1.69% vs
+0.46% (+1.23%); quarters of the difference +0.52/+1.31/+1.26/+0.98% (all
positive); paired t=2.09 over 119 months, A beats B in 57% of months. **Random
portfolios show no such phase effect (+1.24% vs +1.19% per month)** so the
timing dependence is a property of the IBS-picked stocks, not of the universe
having a month-end drift. A plausible mechanism is a turn-of-month flow effect
(SIP/institutional inflows concentrated in the first days of the month, end-of-
month window dressing) acting on just-oversold names; the Sixth entry found no
comparable calendar effect on the index itself, so it would be specific to
oversold stocks. The mechanism is NOT tested here; only ~120 monthly
observations exist and no independent sample.

**Implications, most important first.**
1. **The live paper tracker's first record (2026-09-17) was logged in the
   backtest's worst neighbourhood** (j=9: 7.0%/yr, p=0.90) — 9 trading days
   before month-end. Both trackers (`paper_track_ibs_rotation*.py`) now say in
   their docstrings to run on the LAST FEW TRADING DAYS of the month; the next
   record should be logged at the end of September/October so the forward test
   matches the phase the backtest's edge lives in. Logs are not edited; the
   2026-09-17 record simply becomes a mid-month sample the month-end record
   marks to market (13 trading days, a stub period, not comparable to the
   backtest's monthly figures).
2. **The standing finding should be re-read.** "IBS rotation earns ~20%/yr" is
   true for month-end-window entries; averaged over rebalance timing it is
   ~15%/yr against ~13.5% random, with no phase-averaged significance. The gate
   (Sixty-first/Sixty-second), the slippage test (Sixtieth), the fill-lag fix
   (Fifty-ninth) and every survivorship/widening result all used the month-end
   grid and are best read as conditional on that phase.
3. **Multiple comparisons, again.** 84 phase/anchor cells were run and are NOT
   individually registered (noted in `multiple_comparisons.py`); with them the
   honest Bonferroni family is m>=161 (threshold ~0.0003) and the smallest
   p among them, 0.003 (IBS j=3, itself a post-hoc-selected anchor), fails it.
4. **Do not tune the anchor.** j=3 (24.4%/yr, DD 23.6%) is the best cell of a
   21-cell scan; picking it would be exactly the curve-fit this project's
   methodology exists to catch. The defensible claim is the window (j=0..4),
   and only as a hypothesis for the forward record to test.

**Net verdict.** The standing candidate is more fragile than the previous
entries presented, but not dead: it shows a specific, halves-and-quarters-
consistent, IBS-specific concentration of its edge at month-end entries with a
plausible (untested) flow mechanism. It is now best described as "an oversold-
bounce effect around the turn of the month that has paid ~20%/yr at month-end
entries in 2016-2026," not a general cross-sectional mean-reversion premium.
**Still not declared tradable; no count change (58 -> 60 mechanisms from the
Sixty-third entry stands).** Next steps: (1) log the next tracker records at
month-end; (2) test the turn-of-month hypothesis on an independent sample, e.g.
apply the identical month-end IBS rotation to a different NSE universe (Nifty
Next 50 / midcaps if a constituent list becomes available) or to earlier years
if a point-in-time universe ever exists; (3) test whether the effect is really
about the FIRST days of the month (holding period ends) rather than the entry
day: hold windows anchored on the calendar, not just entries.


## Sixty-fifth: testing the Sixty-fourth entry's month-end window on the decade BEFORE it was found — same sign, about a third the size, not significant

The Sixty-fourth entry's "last 5 trading days of the month" window was defined
on 2016-2026 after seeing a 21-anchor scan, so it is a hypothesis. The only
independent-in-time sample available is the earlier decade (`--oos`: same 52
stocks, ~20 years of Yahoo history, 46 of 52 with prices by 2008; today's
constituents, so survivorship is worse early — it lifts every rebalance phase
alike, and the quantity tested is the phase DIFFERENCE plus a random-portfolio
phase control). IBS(5), top_k=5, lag-1 fill, monthly, mean monthly return of the
last-5-days anchors (j=0..4) vs the mid/late-month anchors (j=7-9, 15-18):

| period | months | last-5-days | mid/late | difference | paired t | A>B | random portfolios (same windows) |
|---|---|---|---|---|---|---|---|
| 2007-09 to 2016-09 (OUT of sample) | 106 | +2.04%/mo | +1.66%/mo | **+0.37%** | 0.59 | 52% | +1.44% vs +1.40% |
| 2016-09 to 2026-09 (where found) | 119 | +1.86%/mo | +0.85%/mo | +1.01% | 2.79 | 62% | +1.21% vs +1.20% |

Out-of-sample quarter-by-quarter differences: +0.81%, +0.60%, +0.30%, -0.24%
(three of four positive, decaying toward the later years). **Reading:** the
sign replicates and the random-portfolio control is again flat, so the timing
dependence still belongs to the IBS-picked stocks, but the size is a third of
the in-sample figure and is statistically indistinguishable from zero (t=0.59).
That is what regression to the mean after post-hoc selection of the best window
looks like, and it is also what a modest real turn-of-month effect looks like;
120 further months cannot tell them apart. Also worth recording: IBS earns
+1.66%/month even in its worst-phase months in the early decade (~22%/yr), and
random portfolios earn +1.40%/month (~18%/yr) — the 2007-2016 universe is
heavily survivorship-flattered (2009 recovery, today's winners), so absolute
returns from that sample must not be compared with the later decade.

**Net effect on the standing candidate.** The month-end concentration is
supported in direction by an independent sample but not confirmed; the honest
description is "IBS rotation's edge is *larger* at month-end entries, by an
amount that is ~+1.0 points/month in the decade it was found and ~+0.4 points/
month (t=0.6) before that." The forward record (now to be logged at month-end,
Sixty-fourth entry) is the remaining test. No count change; still not declared
tradable. Remaining open threads: whether the effect is about the ENTRY day or
the calendar of the HOLDING window (hold windows anchored on the calendar); an
independent universe if a constituent list becomes available.


## Sixty-sixth: where in the month the IBS edge accrues — the first ~5 trading days after a month-end entry; that short-hold version replicates on the earlier decade (p=0.0013) while the full-month hold does not (p=0.26), but its economics are thin

Follow-up to the Sixty-fourth/Sixty-fifth entries' open thread (entry day vs
holding window). Three steps, the first two exploratory on 2016-2026, the third
an independent-sample test on 2007-2016 (`probe_reversal_rotation.py --horizon`,
`--hold`, `--oos-horizon`; same 52 stocks, IBS(5), top_k=5, lag-1 fill).

**1. Excess-return curve by holding horizon** (gross: IBS picks minus the
equal-weight return of every eligible stock over the same days; enter at the
lag-1 close after the ranking date; mean over ~119 months). Month-end entry (j=0),
2016-2026: h=1 +0.20% (t=2.7), h=3 +0.39% (3.0), h=5 +0.45% (2.5), h=10 +0.62%
(2.7), h=21 +0.55% (1.6), h=25 +0.71% (1.9); both halves positive at every h
through 16. **Mid-month entry (j=9): zero to negative at every horizon** (h=5
-0.19%, h=21 -0.40%). The whole month's gross excess (~+0.5-0.7%/month, i.e. the
~6.5-point annual gap to random) accrues in the first 5-10 trading days; days
10-25 add nothing.

**2. Post-hoc policy: hold h days from the month-end entry, then cash** (net of
costs, random control uses the identical hold; 1,500 seeds; top_k=5). 2016-2026:
5d 6.15%/yr, DD 21.7%, p=0.0020; 8d 8.29%, DD 16.5%, p=0.0020; **10d 11.42%/yr,
DD 20.3%, Calmar 0.56, all four quarters positive, p=0.0013 (random same-hold
3.64%/yr)**; 13d 10.46%, p=0.021; 21d 18.65%, DD 38.7%, p=0.060 (the standing
form). top_k=8: 5/8/10/13/21d p = 0.0033/0.0047/0.0127/0.0326/0.0360. With the
Fortieth entry's four blowups added (56 stocks): hold 10d 17.64%/yr, DD 21.9%,
Calmar 0.80, p=0.0007 (top_k=5) and 14.95%/yr, p=0.0007 (top_k=8). The short
hold is far more significant than the full month because its random benchmark
has half the market exposure: same ~0.5%/month excess, much less noise in the
comparison. This is a statement about the TEST, not a bigger edge.

**3. Independent-sample test, 2007-09 to 2016-09 (106 months; the hypothesis was
formed on 2016-2026 only).** Horizon curve at month-end entry: h=1 +0.20% (t=2.2),
h=3 +0.46% (2.5), **h=5 +0.61% (t=2.75, halves +0.51%/+0.72%)**, h=10 +0.39%
(1.3), h=21 +0.31% (0.8) — same shape and, at h=5, the same size as the later
decade (+0.45%). Mid-month entry: a 1-3 day bounce (h=3 +0.33%, t=1.8) that is
gone by h=5 (-0.06%). Policy scored on the earlier decade: **hold 5d 3.82%/yr vs
random same-hold -3.69%/yr, p=0.0013** (later decade p=0.0020); hold 10d p=0.079;
hold 21d 16.39% vs 14.00%, **p=0.26**. So the data supports about 5 days better
than 10, and the FULL-MONTH form of the standing candidate does not replicate
out of sample while the short month-end bounce does.

**How to read it.**
- *Statistically:* a specific out-of-sample confirmation of a pre-stated
  hypothesis (j=0, h=5) at p=0.0013; three horizons were looked at on that
  sample (5, 10, 21), so a x3 correction gives ~0.004. That is a real
  replication, the first in this project's rotation family. Against the global
  ledger it is not: `multiple_comparisons.py` now holds 90 registered p-values
  plus 84 unregistered scan cells; Bonferroni ~0.00056 (or ~0.0003 with the scan
  cells) is below the 1,500-seed resolution floor of 0.00067, so nothing in the
  rotation family CAN pass that bar with this test. Both views are stated because
  neither alone is the honest one.
- *Economically:* thin. 5-day hold nets 3.8%/yr (2007-16, DD 37%, first half -8%)
  and 6.1%/yr (2016-26) on capital deployed ~25% of the time; the gross excess is
  ~+0.45-0.6% per 5-day trade against an assumed 0.43% round-trip cost, so any
  execution cost beyond the model (the Sixtieth entry's spread proxy cannot
  rule out ~0.1% per leg) eats a large share of it. The random control pays the
  same costs, so the p-values are unaffected, but the absolute P&L is what the
  costs decide.
- *Survivorship:* the earlier decade uses today's constituents and a
  loser-bounce strategy is the most exposed to that bias (delisted losers are
  absent), which inflates the 2007-16 result more than the later one; the
  four-blowup stress is not informative for that window (those names were alive
  and stable then).
- *Mechanism:* the bounce concentrated in the first days after a month-end
  entry fits a turn-of-month flow story but is untested; an unrelated 1-3 day
  bounce exists at other times too (mid-month h=1-3 in the earlier decade).

**Net verdict.** The month-end oversold-bounce is now the best-supported
statistical statement about IBS rotation (confirmed on an independent decade
after correction for the horizons looked at) and a materially different one from
"~20%/yr rotation": it is a ~5-day, ~+0.5% gross-per-trade effect at the turn of
the month, with thin net economics. The full-month standing candidate does not
replicate out-of-sample. **Not declared tradable; no count change.** Concrete
implications: (1) the paper trackers should also record the price at +5 and +10
trading days after each month-end entry (currently they mark only at the next
run) so the forward record tests the short-hold form directly; (2) a realistic
execution-cost measurement for oversold names at the close (the one input
that decides whether ~0.5% gross per trade is tradable) is now the most valuable
missing data; (3) any live decision should be sized to a strategy that earns
single-digit percent per year net, not ~20%. 210 tests pass (1 new: hold logic).


## Sixty-seventh: the unhedged paper tracker now records the short-hold hypothesis directly — no extra runs needed

Implements the Sixty-sixth entry's first implication. `paper_track_ibs_rotation.py`'s
`mark_to_market` step now also writes, into the record it closes, a
`horizon_returns` block computed from the price history available at mark time:
for h in {5, 10} trading days, the gross mean return of the logged picks from the
lag-1 fill (the close after the record's date, as in the backtest), the
equal-weight return of every symbol in the 52-stock universe over the same days,
and their difference (`excess`). Horizons not yet elapsed are omitted, never
guessed. Because it uses only prices that exist at mark time, the forward test of
"the edge is the first ~5 trading days after a month-end entry" needs no
additional tracker runs; each month-end record contributes one observation to be
compared with the backtest's +0.45-0.61% (5d) and +0.39-0.62% (10d) gross excess.
Dry-run against the real 2026-09-17 record (log untouched): `{}`, correct — only
one trading day has elapsed. 1 new test (lag-1 fill, universe baseline, unelapsed
horizons omitted); 211 tests pass. The hedged tracker is unchanged (its hedge
leg changes the return being measured). No verdict change: 60 mechanisms tested,
not declared tradable.


## Sixty-eighth: the same tests on 54 different NSE stocks — the short-hold month-end effect does NOT appear, and it is diluted inside the original universe; the Sixty-sixth entry's confirmation is real in time but bound to one stock set

The Sixty-sixth entry's short-hold effect replicated on the EARLIER DECADE of the
same 52 stocks. The complementary independence test is different STOCKS in the
same period. Universe B (`UNIVERSE_B` in `probe_reversal_rotation.py`, 54 names
with data, ZERO overlap with the 52: Nifty-Next-50/mid-cap names such as
AMBUJACEM, BANKBARODA, BEL, DLF, GAIL, HAVELLS, INDIGO, PFC, SIEMENS, ...; today's
constituents, same survivorship caveat) was run through the identical tests with
nothing re-tuned (`--universe-b`; lag-1 fills, 2016-2026, 1,500-seed control).

**Universe B results.**
- Full-month IBS(5) rotation: top_k=5 15.61%/yr (DD 37.1%, all four quarters
  positive) vs random 12.62%, **p=0.187**; top_k=8 13.96% vs 11.88%, p=0.235.
  Positive, not significant.
- Horizon curve, month-end entry: h=1 +0.10% (t=1.5), **h=5 -0.06% (t=-0.4),
  h=10 -0.12% (t=-0.6)**, h=21 +0.39% (t=1.3). The 5-10 day excess the original
  universe showed is absent (mid-month is also ~0).
- Hold-h-then-cash: 5d 0.33%/yr, **p=0.64**; 8d p=0.36; 10d p=0.70; 13d p=0.73;
  21d 14.35% vs 11.69%, p=0.20.

**Inside the original 52** (same window, the two sub-universes separately;
random control within each): 40 large caps (top_k=5): hold 5d 3.61%/yr, **p=0.016**,
10d p=0.30, 21d p=0.20; horizon excess at h=5 +0.28% (t=2.0), roughly half the
combined figure. 12 small/mid caps (top_k=3): hold 5d p=0.14, 10d 9.12%/yr
**p=0.076**, 21d 21.87% p=0.16; excess +0.47% at h=10 (t=1.5). The combined
universe's p=0.0013 is stronger than either half's (0.016, 0.076): the effect is
partly a property of the MIX, i.e. of ranking IBS across a heterogeneous
large/small-cap set, not something either sub-universe delivers on its own.

**Reading.** The short-hold month-end result (Sixty-sixth) has now been tested
on two independence axes: time (same stocks, earlier decade: replicates,
p=0.0013) and stocks (different names, same decade: absent, p=0.64). A generic
"oversold stocks bounce in the first week of the month" effect would have to
appear on both; it appears on one. The candidate explanations that remain
are all uncomfortable: (a) the effect is real but specific to characteristics
of this particular 52-name set (mix of index heavyweights and a few small/mids),
(b) it is a chance finding whose earlier-decade "replication" benefits from
survivorship bias that inflates loser-bounce strategies on today's constituents
in exactly that decade (universe B suffers the same bias in the later decade
and shows nothing, which weakens this explanation but does not remove it), or
(c) it is a small real effect that the noisier universe B cannot resolve. The
index-fund/ETF-flow story that motivated a large-cap-concentrated effect is NOT
supported: the 40 large caps alone show about half the effect of the mix.
**Confidence in the month-end short-hold effect drops from "replicated" to
"unproven, stock-set-specific."** The full-month standing candidate is no better
on B (p=0.19-0.24).

**Multiple comparisons.** 13 more p-values registered (m=103), plus the 84
unregistered scan cells; none of the new ones is significant except the
large-cap 5d cell (0.016), which is not below any corrected threshold.
211 tests pass. **No count change (60 mechanisms tested); IBS rotation remains
the sole standing finding, now with a further-narrowed evidence base; not
declared tradable.** What would change this: the forward record (Sixty-seventh
entry now records the 5d/10d excess at every month-end) and any independent
constituent list that includes delisted names.


## Sixty-ninth: completing the stocks-by-time table for the month-end short-hold effect — present in 3 of 4 cells, absent in the one cell least exposed to survivorship bias

The Sixty-sixth (same stocks, earlier decade) and Sixty-eighth (different stocks,
same decade) entries each filled one off-diagonal cell. This fills the last one:
universe B (54 different NSE names, 48 with prices by 2008) on 2007-09 to 2016-09,
identical tests (IBS(5), top_k=5, lag-1 fill, month-end entry, hold h then cash,
1,500-seed same-hold control).

| | 2007-09 to 2016-09 | 2016-09 to 2026-09 |
|---|---|---|
| **A: original 52** | hold 5d p=**0.0013** (3.82% vs -3.69%); 5d excess +0.61%, t=2.75 | hold 5d p=**0.0020** (6.13% vs 0.66%); 5d excess +0.45%, t=2.5 (where it was found) |
| **B: 54 different names** | hold 5d p=**0.0093** (1.96% vs -3.56%); 5d excess +0.46%, t=1.82; 10d p=0.069; 21d 20.60% vs 13.63%, p=0.069 | hold 5d p=**0.64** (0.32% vs 1.21%); 5d excess -0.06%, t=-0.4 |

**Reading.** The effect is present in three cells at very similar size (+0.45-0.61%
gross excess at day 5) and absent in one. That is a different picture from the
Sixty-eighth entry's "specific to one stock set": in the EARLIER decade both
independent stock sets show it, in the LATER decade only the set it was found on
does. Three explanations fit and the data cannot separate them: (a) a real effect
that has weakened in the later decade for second-tier names (plausible if
oversold-bounce liquidity provision at the turn of the month got more competed
away, but that is a story, not a test); (b) survivorship inflation: the earlier
decade uses today's constituents for BOTH sets and a loser-bounce strategy is the
strategy most flattered by that bias (delisted losers are absent), so the two
earlier-decade cells are the two least trustworthy, and the one cell that is both
fresh in stocks AND has the smallest survivorship exposure (B, later decade) shows
nothing; (c) chance plus post-hoc selection of the window on set A's later decade.
Explanation (b) predicts exactly this table; (a) and (c) also do.

**Net.** Three of four cells support the effect, but the only cell that is
independent of both the discovery data and the survivorship problem does not. It
stays "unproven"; the forward paper record (Sixty-seventh entry) and any
delisting-inclusive universe are the tests that would separate (a)-(c). 3 more
p-values registered (m=106). 211 tests pass. **No count change (60 mechanisms
tested); IBS rotation remains the sole standing finding; not declared tradable.**
Standing lesson for this project's method: a universe of today's constituents
makes every loser-bounce / oversold-ranking result in an EARLIER period
unreliable in the favourable direction, so an earlier-period replication of such a
strategy is weaker evidence than it looks, and a fresh-stocks-and-recent-period
failure is stronger evidence than it looks.


## Seventieth: multi-asset ETF rotation (NIFTYBEES / GOLDBEES / cash) — the timing rules add nothing; a plain 50/50 equity-gold portfolio has the good risk profile, and it is a diversification result, not an edge

A different corner from every stock-selection entry: survivorship-free (the funds
exist and always did), no lot-size wall (single shares), and no ranking of hundreds
of names. `probe_etf_rotation.py` (docstring fixed before running): NIFTYBEES
(equity), GOLDBEES (gold), LIQUIDBEES (cash proxy), 2009-01 to 2026-09 (200
month-end decisions after a year of warm-up), decisions at the month-end close,
filled at the NEXT close, 0.1% per leg on every change of holding, circular-
rotation control on the decision series (2,000 draws, keeps how often each
holding is chosen, breaks its timing). Pre-registered: A. dual momentum
(Antonacci-style; higher trailing-L-month return of the two, cash if <= 0),
L in {6, 9, 12}; B. per-asset trend gate (50/50, each half held only while its
close > its L-day SMA, else cash), L in {100, 150, 200}.

| | return/yr | max DD | Calmar | halves | quarters | p(final wealth) / p(Calmar) |
|---|---|---|---|---|---|---|
| NIFTYBEES buy&hold | 10.71% | 31.0% | 0.35 | +119/+147% | +33/+65/+70/+45% | - |
| GOLDBEES buy&hold | 13.18% | 23.9% | 0.55 | +72/+352% | +66/+3/+60/+183% | - |
| **50/50 monthly-rebalanced** | **12.76%** | **11.4%** | **1.12** | +106/+255% | +54/+34/+71/+107% | - |
| dual momentum 6m | 12.97% | 18.5% | 0.70 | +130/+228% | +26/+82/+87/+76% | 0.147 / 0.073 |
| dual momentum 9m | 11.28% | 18.2% | 0.62 | +109/+182% | +35/+55/+46/+92% | 0.319 / 0.107 |
| dual momentum 12m | 13.25% | 18.6% | 0.71 | +132/+240% | +58/+48/+25/+171% | 0.172 / 0.046 |
| trend gate SMA100 | 8.96% | 9.9% | 0.91 | +46/+184% | +20/+21/+56/+82% | 0.347 / 0.130 |
| trend gate SMA150 | 9.62% | 11.3% | 0.85 | +72/+166% | +31/+31/+43/+86% | 0.300 / 0.306 |
| trend gate SMA200 | 9.66% | 9.4% | 1.03 | +67/+176% | +33/+26/+47/+87% | 0.497 / 0.078 |

**Neither timing family beats the static portfolio** (Calmar 0.62-0.71 for dual
momentum, 0.85-1.03 for trend gates, vs 1.12 for 50/50) and none is distinguishable
from rotating the same decisions randomly (p(final wealth) 0.15-0.50; the best
p(Calmar) is 0.046 for dual momentum 12m, uncorrected and not near a corrected
threshold). There is no timing edge here.

**What the 50/50 is, checked directly.** Monthly-return correlation NIFTYBEES vs
GOLDBEES -0.19. Sub-periods (50/50 | NIFTYBEES | GOLDBEES CAGR, 50/50 max DD):
2009-2013 gold bull: 12.52% | 6.38% | 16.81%, DD 9.4%; 2013-10 to 2019-05 gold
flat: **6.95%** | 12.59% | 0.47%, DD 10.0%; 2019-06 to 2026 gold bull: 17.72% |
11.54% | 22.32%, DD 11.4%. Rolling 3-year CAGR: 50/50 worst +1.54%, median 10.74%,
7% of windows below 5%/yr; NIFTYBEES worst -1.82%; GOLDBEES worst -8.21%, 37% of
windows below 5%. So the portfolio's drawdown control (9-11% in every regime,
including the gold-flat one) is robust to gold's regime, but its headline return
is carried by the two gold-bull windows (in the flat one it trailed equity by ~5.6
points/yr). This is the classic equity-gold diversification result, not a
strategy; the number to remember is the drawdown (~11%), not the 12.8%.

**Caveats.** (1) Drawdown is measured at month-end points. (2) `LIQUIDBEES`'
Yahoo close returns only 3.03%/yr because it distributes/rolls its yield, so
the cash leg is understated (real liquid-fund yield ~5-6%); this penalises the
timing rules (which hold cash 26-34% of the time) by roughly 1-2 points/yr, not
enough to close a Calmar gap of 0.1-0.5 but it means they are not tested at their
best. (3) Gold has had one of its strongest runs on record in this sample
(and the rupee's slide in 2025-26 adds to INR gold returns): the equal-weight
portfolio has no protection against a long gold bear and the data contain only one
gold-flat stretch (2013-2019). (4) 2009 start: the 2008 crash is not included.
(5) Costs of 0.1% per leg are generous for these liquid funds but small next to
the return gaps discussed.

**Multiple comparisons.** 6 more p-values registered (m=112 + 84 unregistered
scan cells); none significant. 213 tests pass (2 new: fill lag and turnover cost).
**Net verdict: two more mechanisms rejected (dual momentum, per-asset trend
gating, both with no timing edge over random); 62 mechanisms tested; the
static equity-gold allocation is recorded as a risk-management observation, not
a finding of alpha. IBS rotation remains the sole standing stock-selection
finding; not declared tradable.**


## Seventy-first: cross-sectional momentum and reversal across four NSE ETFs — a null; one cell at p=0.046 is what six cells produce by chance

Survivorship-free counterpart to the stock-selection rotations: NIFTYBEES, BANKBEES,
JUNIORBEES, GOLDBEES (2009-01 to 2026-09, 200 monthly decisions), next-close fills,
0.1% per leg, random-pick control (k random ETFs each month, same costs, 1,000
draws). Pre-registered (`probe_etf_rotation.py --multi`): momentum L in {6, 12}
months and 1-month reversal, top_k in {1, 2}.

Equal-weight all four (monthly): 13.29%/yr, DD 23.1%, Calmar 0.57. Momentum: 6m
top_k=1 16.21%/yr, DD 33.2%, Calmar 0.49, random-pick 11.41%, **p=0.046**; 6m top_k=2
13.49%, DD 17.3%, Calmar 0.78, p=0.21; 12m top_k=1 10.62%, p=0.50; 12m top_k=2 13.81%,
DD 17.8%, Calmar 0.78, p=0.16. 1-month reversal: top_k=1 7.06%, p=0.85; top_k=2 10.81%,
p=0.67 (worse than random). Only one of six cells is under 0.05 uncorrected, the
number chance alone produces at that rate, and its 33% drawdown is worse than
the equal-weight portfolio's; with the top_k=2 versions at Calmar 0.78 the risk-
adjusted ordering is the reverse of the raw-return ordering. Nothing survives
correction (6 more p-values registered, m=118 + 84 unregistered scan cells).
Combined with the Seventieth entry: across NIFTYBEES/GOLDBEES/BANKBEES/JUNIORBEES,
no timing or ranking rule beats holding them; the good risk profile belongs to
holding a mix. **Rejected: 64 mechanisms tested.** The basket-wide-momentum
finding of the Sixty-third entry (underperforms random across 52 stocks) is
consistent with this ETF-level result (momentum at best marginal), while
reversal fails here too, so the stock-level reversal effect is not an
index-level one. Not declared tradable; IBS rotation remains the sole standing
stock-selection finding.


## Seventy-second: the equity/gold allocation and its timing rules on longer histories that contain 2008 — the "11% drawdown" was a post-2009 property; the trend gate helps only where a crash is in sample and only at one length

The Seventieth entry's NSE-ETF sample starts in 2009, so it excludes the 2008 crash
and the 2000-02 bear. Same pre-registered rules (dual momentum 6/9/12m, per-asset
SMA 100/150/200 gate), same fill lag, cost and rotation control, on two
long-history proxies (`probe_etf_rotation.py --proxy nifty|spx`; price indices, no
dividends; cash accrues at a flat 4%/yr): (a) NIFTY 50 index + gold priced in rupees
(GC=F x INR=X), decisions from ~Sept 2008 (a year of warm-up, so only the tail of
the 2008 crash is inside), 216 months; (b) S&P 500 + gold in dollars, decisions from
~Sept 2001, so it contains the 2001-02 tail, the full 2008 crash, and the 2013-2019
gold bear, ~300 months.

| | NIFTY + INR gold, 2008-26 | S&P + USD gold, 2001-26 |
|---|---|---|
| equity buy&hold | 13.14%/yr, DD 32.8% | 8.33%/yr, DD 54.7% |
| gold buy&hold | 14.31%/yr, DD 25.9% | 11.49%/yr, DD 41.7% |
| **static 50/50** | **14.82%/yr, DD 12.8%, Calmar 1.16** | **10.60%/yr, DD 29.1%, Calmar 0.36** |
| equity/gold monthly correlation | -0.22 | +0.07 |
| dual momentum 6/9/12m (Calmar; p final wealth) | 0.50/0.69/0.58; 0.26/0.31/0.33 | 0.30/0.39/0.38; 0.19/0.23/0.36 |
| trend gate SMA100 | 11.20%/yr, DD 11.7%, Calmar 0.96; p 0.25 | 7.38%, DD 17.1%, Calmar 0.43; p 0.63 |
| trend gate SMA150 | 9.93%, DD 14.8%, Calmar 0.67; p 0.78 | **9.86%, DD 12.0%, Calmar 0.82; p(wealth) 0.023, p(Calmar) 0.001** |
| trend gate SMA200 | 10.25%, DD 11.4%, Calmar 0.90; p 0.74 | 9.40%, DD 15.6%, Calmar 0.60; p 0.12 |

**Corrections to the Seventieth entry.**
1. *The static 50/50's ~11% drawdown belongs to the post-2009 window.* Once 2008 is
   in the sample (S&P/USD proxy), equity and gold fell together for part of it and
   the 50/50 drawdown is 29.1%, still roughly half the equity drawdown (54.7%) and
   better than gold alone (41.7%), Calmar 0.36 vs 0.15/0.28, but nowhere near
   11%. The rupee sample (2008-26) keeps 12.8% because rupee depreciation lifted
   INR gold through the 2008-09 dip. Rolling 3-year worst: S&P 50/50 -2.38%
   (equity -18.4%, gold -14.8%), 20% of windows below 5%/yr, not the 7% seen on the
   ETF sample. Quote the drawdown as "roughly half of equity's, and 11-13% only in
   the post-2009 windows."
2. *Timing rules: still no edge on the NSE-style sample, and one significant cell on the
   S&P.* Dual momentum never improves on static 50/50's Calmar and never beats the
   rotation control (p 0.19-0.36). The trend gate on NIFTY+INR gold lowers Calmar
   versus static (0.67-0.96 vs 1.16; p 0.25-0.78). On the S&P sample, where a 2008-
   sized crash is present, SMA150 lifts Calmar from 0.36 to 0.82 (DD 29.1% -> 12.0%)
   at p(wealth)=0.023, p(Calmar)=0.001; SMA100 (0.43, p=0.63) and SMA200 (0.60,
   p=0.12) do not. **The gate's value is real only when a large equity-and-gold
   crash is inside the window and depends heavily on the length** — the same
   pattern the Sixty-second entry found for a NIFTY/S&P index gate (best length
   differs by market, SMA200 worst on NIFTY). Not a rule to rely on: at SMA150 the p-
   value is 0.001 uncorrected, and 12 more p-values are registered (m=130,
   Bonferroni ~0.0004; the 1,500-seed floor is 0.0007, so no cell here can clear it).

**Net.** The allocation observation is weaker and more honest than the Seventieth
entry read it: diversifying equity with gold roughly halves the drawdown in both
samples but the 9-13% figure is post-2009. Timing rules still add nothing reliable.
213 tests pass; no count change (64 mechanisms tested, the 2 new families here
extend the Seventieth entry's, they are not new mechanisms). Not declared tradable.
Untested: cash yield above 4%, dividends, taxes, and a gold bear longer than
2013-19 (the 1980-2000 gold bear is outside the available data).


## Seventy-third: cost calibration for the IBS hold policies — real NSE delivery costs are ~0.25% round trip, not 0.4%, which adds ~2 points a year and changes no p-value

The probe family's cost model charges 0.2% per leg (0.4% round trip) plus Rs16 per
pick. Actual NSE cash-delivery charges are roughly 0.13% on the buy (STT 0.1%,
stamp 0.015%, exchange/SEBI fees, GST) and 0.11% on the sell (STT 0.1%, fees),
about 0.25% round trip plus the ~Rs16 DP charge; the old figure was ~0.15
points conservative per round trip. Added `--cost` to `probe_reversal_rotation.py`
and reran the IBS(5) top_k=5, lag-1, month-end hold policies at 0.125% per leg
(random control pays the same cost, so excess-vs-random and every p-value are
unchanged; only absolute returns move):

| hold | 2016-26 at 0.2% -> 0.125%/leg | 2007-16 at 0.2% -> 0.125%/leg |
|---|---|---|
| 5 days | 6.13% -> 8.11%/yr (DD 22% -> 18%) | 3.82% -> 5.77% (DD 37% -> 35%) |
| 10 days | 11.38% -> 13.44% (DD 20%) | 3.83% -> 5.79% (DD 49% -> 48%) |
| 21 days | 18.75% -> 20.92% (DD 39% -> 38%) | 16.39% -> 18.53% (DD 62% -> 61%) |

Random same-hold controls rise by about the same ~2 points, so the ~6-point-per-year
excess of the 5-10 day holds over random is untouched. Two consequences: (1) the
"thin economics" line in the Sixty-sixth entry is somewhat less thin: ~8%/yr
(5d) and ~13.4%/yr (10d) net in the later decade, but 5.8% in both short holds in
the earlier, survivorship-inflated decade, on capital deployed 25-50% of the time;
(2) if the idle capital sat in a liquid fund at ~5-6%, a 5-day hold would add
roughly 4 more points a year (75% of the time x ~5.5%), which is comparable to
the static equity/gold portfolio's return with a higher drawdown (18-35% vs
12-29%). This is arithmetic, not a new test; the ~0.125% figure is my estimate from
published NSE charge schedules (not re-verified against a contract note), and
slippage/impact (the Sixtieth entry could not size it) is not included. No p-value,
count or verdict change. IBS remains unproven and not declared tradable.


## Seventy-fourth: the month-end oversold-bounce on US large caps — a faint echo in the earlier decade, nothing in the recent one

A third independence axis for the Sixty-sixth entry's short-hold effect: a different
MARKET (different investors, calendar structure and flows). `probe_reversal_rotation.py
--us`: 52 current S&P-100-type large caps (AAPL, MSFT, JPM, JNJ, XOM, ... USB) on the
S&P 500 trading calendar, ~20 years, IBS(5), top_k=5, lag-1 fill, month-end entry, the
identical pre-declared tests, US-appropriate costs (0.05%/leg, no DP charge), 1,000-seed
same-hold random control. Same survivorship caveat as every earlier-decade cell.

Horizon curve, gross excess of IBS picks over the equal-weight universe, month-end
entry: 2007-16 h=1 +0.07% (t=1.4), h=5 +0.24% (t=1.8), h=10 +0.25% (t=1.3), h=21
-0.10%; 2016-26 h=1 +0.02%, **h=5 +0.01% (t=0.08)**, h=10 +0.07%, **h=21 -0.71%
(t=-2.3)**. Mid-month entry: ~0 throughout. Hold-then-cash vs same-hold random:
2007-16 hold 5d 3.74%/yr (p=0.040), 10d 6.02% (p=0.056), 21d 6.61% vs 8.51% random
(p=0.66); **2016-26 hold 5d 4.93% (p=0.51), 10d 8.96% (p=0.52), 21d 4.96% vs 14.51%
random (p=0.997)**.

**Combined table for the short-hold month-end effect (hold 5d p-values):**

| universe | 2007-16 | 2016-26 |
|---|---|---|
| NSE original 52 | 0.0013 | 0.0020 (where found) |
| NSE different 54 | 0.0093 | 0.64 |
| US large caps 52 | 0.040 | 0.51 |

Every universe shows something in the earlier decade (three of three); in the
recent decade the only cell with an effect is the one it was found in (0 of 2
independent replications). This is what survivorship inflation of a loser-bounce
strategy on today's constituents in the years around the 2009 recovery predicts,
and it is what a chance finding predicts. It is much less consistent with a
robust market-wide turn-of-month oversold-bounce, which would appear in the recent
decade on fresh stocks and on a different market. In the US universe the full-month
oversold hold is actively worse than random in the recent decade (-9.5 points/yr,
p=0.997), i.e. buying just-oversold large caps for a month has been a bad trade
there, the opposite of the NSE sign.

**Net.** Confidence in the short-hold month-end effect falls further: from
"replicated 3 of 4" (Sixty-ninth) to "found once, echoing weakly in the earlier
decade in all three universes and absent in the recent decade in both independent
ones." It stays "unproven" and I would now weight it as more likely to be a
finding of the discovery sample than a tradable effect. 6 more p-values registered
(m~136 + 84 scan cells); 213 tests pass; no count change (64 mechanisms tested); not
declared tradable. The forward record (Sixty-seventh entry) remains the arbiter.


## Seventy-fifth: a descriptive static equity/gold grid on histories that contain the full 2008 crash — the drawdown scales with the equity share, annual rebalancing is not worse than monthly, and the "12.8%" was an artefact of where the sample started

No test statistic here (static weights have no timing to test), just the numbers a
person choosing weights actually needs, on the longest data available
(`probe_etf_rotation.py --alloc`): Sensex + gold in rupees (GC=F x INR=X), 2003-12 to
2026-09 (273 months, the full 2008 crash), and S&P 500 + gold in dollars, 2000-08 to
2026-09 (313 months); price indices (no dividends), cash leg at a flat 4%/yr, month-end
closes, 0.1% per leg on rebalancing turnover, annual = December month-ends.

| weights / rebalance | Sensex + INR gold: return, max DD, Calmar, worst rolling 3y | S&P + USD gold: return, max DD, Calmar, worst rolling 3y |
|---|---|---|
| 30% equity / 70% gold, monthly | 14.64%, 19.7%, 0.74, -2.7% | 10.24%, 26.1%, 0.39, -7.2% |
| 30/70, annual | 14.85%, 17.2%, 0.86, -2.8% | 10.31%, 26.5%, 0.39, -6.3% |
| 50/50, monthly | 14.24%, 27.0%, 0.53, +1.3% | 9.37%, 26.1%, 0.36, -2.4% |
| 50/50, annual | 14.55%, 23.8%, 0.61, +1.1% | 9.45%, 26.7%, 0.35, -1.4% |
| 70% equity / 30% gold, monthly | 13.52%, 37.2%, 0.36, +3.3% | 8.32%, 36.3%, 0.23, -6.8% |
| 70/30, annual | 13.86%, 33.8%, 0.41, +3.8% | 8.39%, 34.6%, 0.24, -6.0% |
| 40/40/20 cash, monthly | 12.28%, 21.5%, 0.57, +1.9% | 8.41%, 20.8%, 0.40, -1.1% |
| 40/40/20 cash, annual | 12.60%, 18.5%, 0.68, +1.8% | 8.50%, 20.9%, 0.41, -0.3% |

**What it says.**
1. *Corrects the Seventieth/Seventy-second entries' headline again.* With the full 2008
   crash in the sample, a 50/50 equity/gold portfolio had a 24-27% max drawdown on
   both markets (equity alone: ~55% for the S&P), not 11-13%. The NIFTY-proxy 12.8%
   in the Seventy-second entry started its decisions in September 2008, after the first
   leg of the fall, and the NSE-ETF ~11% starts in 2009. README corrected.
2. *Drawdown scales with the equity share* (Sensex: 17-20% at 30% equity, 24-27% at
   50%, 34-37% at 70%; S&P: 26%, 26%, 35-36%) and the worst rolling 3-year return
   improves with equity share on the Sensex sample, so the weights are a risk
   preference, not a discovery. Adding a 20% cash sleeve lowers the drawdown to
   ~18-21% at a cost of ~1.5-2 points/yr.
3. *Annual rebalancing is at least as good as monthly* (equal or better return in all
   8 pairs by 0.07-0.34 points; drawdown lower in 5 of 8 and slightly higher, by 0.1-0.6 points, in the other 3, all S&P): fewer trades,
   fewer taxable events, no reason to rebalance monthly.
4. *More gold looks better here because gold beat equities in this sample* (gold
   from ~$270 to ~$4,400 and the rupee's decline both add to INR gold; S&P
   returned 8.3%/yr on this data). The grid cannot tell you gold will keep doing
   that; the Sensex 30/70 winner is a bet on it. The one gold-flat stretch
   (2013-2019) is where the 50/50's return trailed equity's (Seventieth entry).

**Not a strategy claim and not a recommendation:** dividends, taxes, expense ratios,
tracking error of the ETFs, and a gold bear longer than 2013-19 are not modeled. 213
tests pass (no new logic worth a test: the grid reuses `stats`, tested previously); no
count change (64 mechanisms tested). Its use is as the risk-management baseline any
future candidate should be compared against: a 50/50 annual-rebalance
equity/gold portfolio earned ~9.5-14.6%/yr at a ~24-27% drawdown with no signal at
all.


## Seventy-sixth: fixing the bugs an independent code review found in `probe_reversal_rotation.py` — three change published numbers, none reverses a conclusion, one strengthens the trend gate and one weakens the month-end concentration

An independent `/code-review` (medium) of the probe file behind Entries 59-75
found eight issues. Each was verified against the code; the three that affect
reported results were rerun with the fix.

**1. `gate_test` forced early months into cash (Entry 61).** `(nifty > sma).fillna(True)` never
fills anything (comparison with a NaN SMA is False, not NaN), and the SMA was
computed after NIFTY was cut to the 10-year matrix, so the first 100-200 rows had a
NaN SMA and the first 5-9 month-ends were counted as risk-off. Fixed: the SMA is
computed on the full NIFTY history and NaN is treated as risk-on. Corrected results
(52 stocks; months in cash 37/33/29 instead of 40/38/38):

| top_k / SMA | return/yr (was) | max DD | Calmar (was) | p(DD) (was) |
|---|---|---|---|---|
| 5 / 100 | 15.78% (15.11) | 13.5% | 1.17 (1.12) | 0.027 (0.040) |
| 5 / 150 | 15.24% (14.79) | 14.8% | 1.03 (1.00) | 0.031 (0.062) |
| 5 / 200 | 15.27% (13.97) | 17.8% | 0.86 (0.78) | 0.077 (0.116) |
| 8 / 100 | 13.79% (13.58) | 11.4% | 1.21 (1.19) | 0.013 (0.016) |
| 8 / 150 | 13.62% (12.48) | 16.1% | 0.85 (0.77) | 0.054 (0.100) |
| 8 / 200 | 13.80% (10.75) | 17.5% | 0.79 (0.60) | 0.078 (0.153) |

Under the blowup-stress universe: top_k=5 SMA100/150/200 now 18.74/19.13/19.29%/yr,
DD 20.8/14.8/15.1%, Calmar 0.90/1.29/1.27 (ungated 0.60), p(DD) 0.124/0.018/0.019;
top_k=8 unchanged in kind (Calmar 0.87/0.61/0.56 vs ungated 0.68: only SMA100 helps,
p(DD) 0.09/0.33/0.39). **The conclusion stands and gets slightly stronger:** Calmar
roughly doubles at top_k=5 in both universes, is not robust at top_k=8 under stress.

**2. The index-gate control paid no switching cost (Entry 62).** The gated series was
charged 0.2% per flip; the random-off-months control was not. Fixed: both pay it. The
drawdown p-values barely move (NIFTY 0.055/0.010/0.160; S&P 0.0005 at all three).
**But the return comparison changes, and Entry 62's sentence "the gate does not time
returns better than random months" is withdrawn for the S&P:** with equal costs the
S&P gate's return beats random cash months at SMA150 (p=0.010) and SMA200 (p=0.002)
(SMA100 p=0.281); NIFTY 2008+ p 0.15-0.40, still not significant. So on 70 years of
S&P data the gate has a modest, statistically supported timing value at 150-200 days
on top of its drawdown effect; on NIFTY it is only a drawdown device. Six return
p-values registered.

**3. Positional alignment in `oos_anchor_test` (Entry 65).** Anchor streams that drop
different first months at the window edge were averaged by array position, pairing
non-matching months. Fixed: streams are keyed by calendar month and only common
months are compared. Corrected: 2007-16 last-5-days +1.98%/mo vs mid/late +1.66%/mo,
diff **+0.32%, paired t=0.49** (was +0.37%, t=0.59), A beats B in 45% of months;
2016-26 (118 months) diff **+1.01%, paired t=1.90** (was t=2.79), 49% of months.
Quarter differences 2007-16: +0.41/+0.84/+0.55/-0.56%; 2016-26: +0.53/+1.29/+1.46/
+0.78%. Random portfolios still show no phase effect. **The month-end concentration is
weaker in its own discovery sample than the Sixty-fourth/Sixty-fifth entries
reported (t 1.9, not 2.8) and the earlier-decade support is weaker still (t 0.5);**
this feeds into the Sixty-eighth to Seventy-fourth entries' conclusion that the
short-hold effect is unproven. (The Sixty-fourth entry's inline paired t=2.09 used the
same positional alignment; treat it as ~1.9-2.1.)

**Other fixes.** The REV_CACHE pickle key now includes the period, stress and universe
suffixes, so a 20y run can no longer read a 10y cache (the earlier-decade results were
produced with separate cache paths and are unaffected; the review's scenario did not
occur here); `--us` now honours `--cost` and `--index-gate` honours `--seeds`;
`load_matrices` fetches the calendar once; `load_us` retries a failed ticker three times
and reports exclusions; a duplicate unreachable `--index-gate` branch and a dead
`260 * 0` were removed. Not done: the reviewer's suggestion to factor the eight copies
of the seeded-random-control block into one helper (worth doing before adding a ninth).

**Independent cross-check (before the review returned):** a from-scratch plain-python
reimplementation using the original family's own `avg_ibs` reproduces the headline
lag-1 IBS(5) number: 20.30%/yr, 39.5% DD vs 20.05%, 39.5% here, so the core simulate/
score/cost logic is not where the errors were. 213 tests pass. No count change (64
mechanisms tested); no verdict change (IBS rotation unproven, nothing declared
tradable); the trend gate's evidence is somewhat better than reported, the month-end
concentration's somewhat worse.


## Seventy-seventh: second independent code review (macro, fear-buy, ETF probes) — one bug had inflated the Entry 58 fear-buy numbers, one would have made the monthly state report print stale data; the fear-buy is now a clean null

A second `/code-review` (medium) covering `probe_macro_analog.py`,
`probe_fear_followup.py` and `probe_etf_rotation.py` returned seven findings. Fixed
and rerun; two matter.

**1. `declustered_events` re-entered mid-spike (Entry 58).** The 21-day gap was measured
from the last accepted EVENT, not from the last "on" day, so inside a long spike (e.g. March-
July 2020, 88 days) it emitted a new "first-crossing" entry every 21 days. Mid-spike
days are exactly where Entry 58 found returns highest (+6.7%), so the event counts,
p-values and the delay curve were contaminated toward a positive result. Fixed: an
event is an "on" day with no "on" day in the previous 21 rows. Corrected results:
- India VIX / 252d median first-crossing events (grid-free): >=1.4 17 events, mean
  -0.00%, p=0.79; **>=1.5 14 events (was 23), mean +0.96% (was +2.38%), 9/14 positive,
  p=0.50 (was 0.063)**; >=1.7 9 events, +2.93%, p=0.092. Unconditional +0.91%. US VIX
  version: +0.13%/+0.04%/-0.72%, p 0.81-0.92. **The real-time fear-buy has no edge.**
- Delay curve (14 events; post hoc): first crossing +0d +0.96%, +3d +1.79%, +5d +3.28%,
  **+10d +5.45% (13/14 positive, worst -2.4%)**, +15d +3.46%: the India persistence
  pattern is still there in the smaller sample, but it is a 14-event, 5-delay post-hoc
  curve.
- **S&P 500 1990-2026 replication: 38 events (was 74)**, unconditional +0.81%: +0d
  +0.92% (p=0.45), +5d +0.26% (p=0.78), **+10d +0.66% (p=0.59)**, +15d +1.10% (p=0.35);
  worst +10d event -13.1% (2008-09-17). The persistence effect does not replicate,
  now unambiguously (it was p=0.14).
- Unchanged: the daily OLS (India VIX-rel t=+4.56 alone, +2.18 alongside drawdown, NW-21)
  and the IBS-overlap result (no declustering involved). Count-matched drawdown-only
  trigger: 14 events +1.47% (p=0.34) vs the VIX trigger's +0.96% (p=0.50).
- Entry 58's headline ("+2.4%, p=0.063") is superseded; Entry 57's "12/12 months +7.4%" was
  already withdrawn by Entry 58 as a grid artefact and the S&P replication is now a
  clear null. 10 p-values in the registry were updated in place.

**2. `--state-only` printed stale data (Entries 61-62 told you to run it monthly).**
`load()` returned `macro_cache.csv` whenever it existed and never refreshed it, so next
month-end the gate and trigger readings would have been the 2026-09-18 values with
no warning except the "As of" date. Fixed: `--state-only` now redownloads, drops a
still-forming today bar (before 16:00 local), and writes `macro_cache_live.csv`
(git-ignored) instead of touching the committed reproducible cache; on a network
failure it prints a warning and falls back to the committed cache. The cache path
is now absolute (module directory). Verified: a refreshed run works.

**Other fixes.** `shift_p` returned the raw fraction (could be exactly 0.0); it now uses
(count+1)/(n+1) (effect on Entry 57's 10,000-seed values <= 1e-4; the phase-robustness
"p=0.0" cells at 300 seeds are really <= 0.0033). `robustness()` mutated module globals
(`OFFSET`, `FEATURES`) with no `try/finally`; now restored on any exit. `probe_etf_rotation`:
max drawdown now includes the starting capital in the peak, and `run()` lets weights
drift so a static monthly 50/50 pays its rebalancing cost like the other paths. Re-running
every ETF mode (NSE ETFs, NIFTY and S&P proxies, the 4-ETF cross-section, the
allocation grid) moved no reported figure by more than 0.05 points/yr or 0.1 pt of
drawdown and changed no p-value beyond the third decimal (e.g. static 50/50 12.76% ->
12.73%; 4-ETF equal-weight 13.29% -> 13.26%); Entries 70-72 and 75 stand as written.
Not done: consolidating the (now three) copies of the rotation-control p-value block
into one shared helper.

**Method lesson, recorded because it has now happened twice:** both code reviews found
bugs that biased results in the direction of a finding (Entry 58's mid-spike
re-entries) or of a cleaner story (Entry 62's unequal cost control), and both were
in scripts whose output I had already written up. Independent review of analysis
code before writing conclusions is now part of this project's method; the checks
that did NOT catch these (unit tests of helpers, an independent reimplementation of
the headline number) are necessary but not sufficient. 214 tests pass (1 new,
1 corrected). No count change; IBS rotation remains unproven, the fear-buy is a null,
nothing is declared tradable.


## Seventy-eighth: third independent code review (PCR probe and the p-value registry) — the registry undercounted the family, one PCR cell sits on the 0.05 line, and the one surviving Bonferroni result is now backed by properly resolved p-values

A third `/code-review` (medium) of `probe_pcr_signal.py` and `multiple_comparisons.py`
found no correctness bug in the core simulation (fill lag, percentile window, OI
parsing and the BH implementation all check out; every registry value it
cross-checked against CLAUDE.md matched) and six issues in the accounting around it.
All fixed.

**1. The registry understated the family, and the report said so nowhere.** The broad-family
table used m = number of registered rows (now 148) while the Sixty-fourth entry's 84 phase/
anchor scan cells were only mentioned in a comment. `multiple_comparisons.py` now
prints a HONEST-family line: 148 registered + `UNREGISTERED_SCAN_CELLS` (84) = m=232,
Bonferroni threshold 0.00022, and lists the rows that pass it. Stale docstring/comment
references (`PVALUES`, "All 7 numbers") fixed.

**2. Six PCR sweep cells were never registered** (the registry held 6 of the 12 configs and its
comment miscounted them). All 12 are now registered, re-derived with a proper
(count+1)/(n+1) p-value (the PCR probe had the same raw-fraction defect fixed elsewhere in
the Seventy-seventh entry): 0.80/13 0.070, 0.80/26 0.053, 0.80/52 0.133, 0.85/13 0.124,
0.85/26 0.051, **0.85/52 0.049-0.053**, 0.90/13 0.124, 0.90/26 0.085, 0.90/52 0.070,
0.95/13 0.118, 0.95/26 0.328, 0.95/52 0.220. **Correction to the Fifty-sixth entry:** it said
"none clears even an uncorrected 0.05". The 0.85/52 cell (40 trades, Rs 51,949, 4.87%/yr,
walk-forward consistent, Q4 +Rs 3,320) is at p = 0.050 with 1,500 seeds, 0.053 with 5,000 and
0.049 with 20,000: on the line, i.e. one cell in twelve at ~0.05, what chance gives. It clears no
corrected threshold (and 0.05 is on the wrong side of it anyway); the PCR rejection stands but
the sentence was inaccurate.

**3. The BH caution comment was stale.** It described only an IBS flip at m>=29; at the current size
BH marks rows the project has rejected on other grounds (Reversal(21) top_k=8, the nested IBS
hold-policy rows) as PASS. The comment now says BH's independence assumption is violated by
nested and resolution-floor rows, that its column is informational only for this family, and
that Bonferroni against the honest m is operative; the report prints that note.

**4. The S&P index-gate drawdown p-values were resolution-floor values.** They were 0.0005 (the
2,000-draw floor), which cannot be compared to an m=232 threshold of 0.00022. Rerun at 20,000
draws (`--index-gate --seeds 20000`): S&P 1950+ drawdown p = **0.0001 (SMA100), <=5e-5 (SMA150),
<=5e-5 (SMA200)**; return p 0.269/0.011/0.001. NIFTY 2008+ drawdown p 0.055/0.0072/0.149.
**Under the honest family (m=232, threshold 0.00022) exactly three rows pass Bonferroni: the three S&P
1950+ index-gate drawdown effects.** The README's "only Bonferroni pass" statement stands, now resting
on resolved p-values; the caveats stand too (random-off-months null ignores that trend-off months
cluster in crashes, which is the gate's whole point; it is a risk overlay of a well-known effect;
best length differs by market).

**5. PCR probe hygiene.** The cached PCR series was loaded whenever the file existed regardless of
`--start/--end` (a `--start 2020-01-01` run would silently simulate 2016+ but annualise from
2020); it now checks coverage and refetches on mismatch. A partial fetch is no longer written to
the cache (a transient NSE failure used to be reused forever with no warning), and an empty
NIFTYBEES price pull raises instead of reporting "0 trades" as a null result.

No count change (64 mechanisms tested); no verdict change; nothing is declared tradable. 214
tests pass. The three reviews together found 8 + 7 + 6 issues; the first two moved published
numbers, this one moved accounting only. Standing practice: independent review of analysis
code and of the significance ledger before conclusions are written.


## Seventy-ninth: published versions of what famous funds and traders do — none is a return source at retail scale; the useful findings are about risk, and a data-quality check that came out clean

Asked to try strategies "the top hedge funds or extremely successful traders use". The
honest scope: the actual books of Renaissance, Citadel or Bridgewater are secret; what
can be tested is the published, public version of each approach. Pre-registered in
`probe_famous_strategies.py` and `probe_vol_breakout.py` before any run, on
survivorship-free instruments (ETFs, indices), lag-1 fills, 0.05%/leg, T-bill cash, and an
independent code review of the probe BEFORE conclusions were written (it found 7 issues,
all fixed and rerun; see "corrections" below).

| approach (who) | what was tested | result |
|---|---|---|
| **Time-series momentum** (CTA core: Winton, Man AHL; Moskowitz-Ooi-Pedersen) | 11 ETFs (SPY EFA EEM VNQ TLT IEF LQD GLD SLV DBC UUP), 2006-08..2026-07, 240 months, L in {126,189,252}, 60d vol | **Long/short 10%-vol (research only, retail can't run it): 3.4-3.9%/yr, Sharpe 0.38-0.49 (equal-weight buy&hold 0.54), max DD 6-10%**. **Long/flat inverse-vol (retail-tradable): 4.6-5.0%/yr, Sharpe 0.67-0.78, DD 6.0-6.7%**, but the no-signal twin (always long, inverse-vol) is 5.86%/yr, Sharpe 0.67, DD 14.2%: the trend signal is a drawdown device (~1pt/yr of return for half the drawdown), not extra Sharpe. Exact rotation-control p(Sharpe) 0.005-0.057 (B), 0.077-0.206 (C) |
| **Risk parity** (Bridgewater All Weather) | inverse-vol SPY/TLT/IEF/GLD/DBC, unlevered, 2007-04..2026-07 | 5.64%/yr, vol 7.1%, Sharpe 0.60, DD 15.3% vs 60/40 SPY/IEF 8.18%, Sharpe 0.68, DD 30.3%, SPY 10.82%, DD 52.9%. Windows: **2008** RP +4.2% vs 60/40 -16.4%, SPY -34.3%; **2022** RP -11.1% vs 60/40 -16.5%, equal-weight -9.0%, SPY -19.0%. Unlevered, it is the lowest-drawdown option with the lowest return and NO Sharpe gain over 60/40; the fund's edge is leverage, which retail does not have |
| **Volatility-managed equity** (AQR/Moreira-Muir) | exposure = min(1, target/21d vol), target 10%/15% | S&P 500 1960+: Sharpe 0.28/0.29 vs 0.29 buy&hold, DD 30.3%/41.5% vs 54.7%, p(Sharpe) 0.35/0.24. NIFTY 2008+: Sharpe 0.35/0.33 vs 0.45, DD 20.7%/27.1% vs 32.2%, p(Sharpe) 0.89/0.98 (worse than random exposure). A drawdown device again, no Sharpe gain |
| **Halloween / "sell in May"** (Stock Trader's Almanac) | hold Nov-Apr, cash May-Oct, T-bill cash and switch costs | **S&P 500 1950+: winter beats summer by +0.73%/month, 77 cycles, year-block bootstrap p=0.0003, positive in 68% of cycles; halves +0.92%/+0.55% (decaying). Nov-Apr-only 8.59%/yr vs buy&hold 8.48%, vol 10.4% vs 14.7%, Sharpe 0.44 vs 0.34, DD 31.9% vs 54.7%.** NIFTY 2008+: gap -0.38%/month, p=0.75, Nov-Apr-only 5.09% vs 7.78% buy&hold |
| **Volatility breakout** (Larry Williams, 1987 champion) | buy at open + k x yesterday's range, sell at close; k in {0.4,0.6,0.8}; long and short; fill at trigger (optimistic) | ^NSEI 2007+: mean per trade -0.02% to +0.02% net of 0.1% costs (p 0.30-0.85 long, 0.32-0.58 short), all negative with 0.1% slippage/leg. SPY 1993+: **-0.10% per trade, p=1.0 at every k, both sides.** Nothing |

**Reading.** None of these beats the plain benchmark on return, and the famous funds' actual edge in these
constructions is leverage and breadth that a retail account cannot replicate (levered risk parity, 10%-vol long/short
across dozens of futures markets). What retail CAN take from them is the same lesson this project keeps
finding: trend/vol filters and diversified allocations are drawdown tools, not return sources, and the
one clean, long-history, statistically strong effect (Halloween on the S&P, p=0.0003) is (a) not present in India,
(b) already halved in its second half, and (c) does not clear the honest Bonferroni threshold
(m=256, 0.00020). The Halloween result is the most interesting single finding: the same total return as
buy&hold at ~70% of the volatility and ~58% of the drawdown, but it is a US result and it is decaying.
The TSMOM equity/bond/gold/commodity universe is US-listed ETFs; an Indian retail investor would need the
overseas-investment route, so even the "retail-tradable" long/flat variant is not directly implementable.

**Data-quality check (prompted by a spurious result).** The vol-breakout probe's first run printed a
NIFTYBEES short result of +0.26-0.31% per trade at p~0. Its unconditional open-to-close mean was -0.41%/day
(about -70%/yr): impossible. Inspection: Yahoo's NIFTYBEES.NS rows include days with Open=High=Low=Close and
open==high-or-low on 42% of days; the ETF's OHLC is corrupt. It was removed and a sanity guard added. Because
every NSE instrument on Yahoo also shows a positive overnight gap and a negative intraday return (^NSEI +0.11%
overnight, -0.069% intraday; RELIANCE +0.125%/-0.048%; SPY +0.03%/+0.02%), and the IBS work depends on Yahoo's
high/low for NSE stocks, the Yahoo OHLC was validated against NSE's OFFICIAL daily bhavcopy (30 random dates
2018-2026 x 10 large caps = 300 symbol-days): **in every row the four Yahoo/NSE price ratios agree to within
0.000%; 21.7% of rows differ only by a split/bonus adjustment factor (Yahoo adjusts history, bhavcopy does not)
and the other 78% match the official Open/High/Low/Close exactly.** So Yahoo's OHLC for NSE large caps IS the
official data: the IBS results are not a vendor artifact, and the overnight/intraday asymmetry is a real property of
NSE opening-auction prints. NIFTYBEES's corruption is specific to that ETF (and its Open-based numbers are not used
anywhere).

**Corrections made from the independent review of this entry's probe (7 findings, all fixed, results rerun).**
(1) The risk-parity stress-window table labelled each month by its decision date, one month early: the first run
printed SPY -7.8% for 2022 (true -18.2%); labelled by fill date it is -19.0%, and the 2008/2013 rows moved too
(risk parity's 2008 went from -2.0% to +4.2%). (2) `rotation_p` drew 2,000 random offsets from ~185 distinct
rotations (implying a false resolution of 1/2001); it now enumerates every rotation exactly with (count+1)/(m+1).
(3) The Halloween resample included the still-forming current month. (4) The Halloween strategy ignored T-bill cash
and switching costs (buy&hold vs Nov-Apr-only was 8.30% vs 6.49% with cash 0; with cash and costs it is 8.48% vs
8.59%: the gap the first version showed against the seasonal rule was an accounting artifact). (5) Fetches now retry
and are cached. (6) Loop-invariant volatilities are precomputed. (7) Duplicated helpers vs the other probes remain
(documented debt).

**Ledger.** 24 more p-values registered (m=172 + 84 unregistered scan cells = 256, Bonferroni 0.00020); the only
rows passing are still the three S&P index-gate drawdown rows. 217 tests pass (3 new). 64 -> 69 mechanisms tested.
Nothing is declared tradable.


## Eightieth: a second batch of famous-fund approaches — currency carry, sector-ETF pairs, short-vol term-structure timing — one thin real effect, one null, one that worked only in a product that no longer exists

Same discipline as the Seventy-ninth entry: rules pre-registered in `probe_famous_strategies2.py`'s docstring
before any run, survivorship-free instruments, lag-1 fills, an independent code review BEFORE the entry
was written (six findings; two biased toward the strategy; all fixed and rerun). Everything here is
retail-inaccessible from India as specified (US-listed ETFs, FX, shorting): research on whether each approach
works, not an implementation plan.

**1. Currency carry** (the classic macro-fund trade). 9 currencies vs USD (EUR GBP JPY AUD NZD CAD CHF NOK
SEK), FRED 3-month interbank rates lagged one month, long top-3 / short bottom-3 carry, dollar-neutral, monthly,
0.03%/leg, 2003-06..2026-07 (278 months). Result: **+2.06%/yr, vol 7.5%, Sharpe 0.31, max DD 30.9%, worst month
-10.4%**; carry income +2.93%/yr, spot P&L -0.57%/yr; skew -0.34; 19 months below -3%; **2008 -27.0%**, 2009
+26.6% (a crash-then-rebound shape), 2013 -5.3%. Halves Sharpe 0.34/0.28 (steady, thin). Control: random
3-long/3-short books. **First version compared net-vs-net and reported p(Sharpe)=0.013, p(wealth)=0.005; the review
showed the random control redraws its whole book monthly and pays several times the strategy's turnover cost,
biasing p in the strategy's favour. Compared gross of cost on both sides (4,000 draws): p(Sharpe)=0.070,
p(wealth)=0.034; random mean Sharpe 0.00.** Also fixed: FRED's GBP series ends 2026-01, so its last 6 months of
carry had been silently frozen at a stale rate; stale rates are now dropped. So carry is a real but thin premium
(~2%/yr, borderline significance) paid for with crash risk; not a return source at this size.

**2. Sector-ETF pairs** (the stat-arb archetype, Gatev-Goetzmann-Rouwenhorst). 9 SPDR sector ETFs, 1999-12..
2026-07, 53 non-overlapping 126-day windows, the 5 closest pairs by 252-day SSD, 2-sigma entry, zero-crossing exit,
0.2% round trip, signal at close t-2 -> position over day t (lag 1). Result: **-0.09%/yr, Sharpe 0.02, max DD 47.4%**;
quarters +29.7%/-26.9%/-14.4%/+16.3% (total P&L per $1 pair capital); vs 5 RANDOM pairs per window (3,000 draws)
**p(Sharpe)=0.65, p(P&L)=0.68** (random pairs earned MORE, +18.6% vs +4.6% total). Null; the classic result that the
pairs premium has vanished since the 1990s reproduces on sector ETFs.

**3. Short-volatility term-structure timing** (the vol-seller's trade). Hold SVXY when ^VIX/^VIX3M < threshold
(contango), else T-bill cash; signal at close t, fill at the next close, earn the following day (two-day lag);
Sharpe in EXCESS of cash; 2011-10..2026-09. SVXY buy&hold 12.23%/yr, vol 54.8%, Sharpe 0.54, **max DD 95.2%**, worst
day -83% (2018-02-06); SPY buy&hold 15.56%/yr, vol 16.7%, **Sharpe 0.85**, DD 33.7%. Rule at ratio<1.0: 19.95%/yr, Sharpe
0.60, DD 65.9%, exact-rotation p(Sharpe)=0.104, worst year 2018 -57%. Rule at ratio<0.9: 21.71%/yr, vol 31.0%,
Sharpe 0.74, DD 38.6%, p=0.073, in the market 60% of days. The term-structure filter did NOT hold SVXY into its -83%
day (the ratio had inverted) but did take the -32% day before it. **The review's structural point:** SVXY became
a -0.5x product on 2018-02-28, and the sample splices the two. In the -0.5x era alone: SVXY buy&hold 11.81%/yr, vol
36.7%, Sharpe 0.42, DD 62.2%; rule at <1.0 **8.39%/yr, Sharpe 0.34, DD 57.6%; rule at <0.9 -0.21%/yr, Sharpe -0.03**,
DD 38.6%. **The 21.7%/yr headline was earned in the -1x era (mostly 2011-2017's calm, the decade before the product
was cut down), and the timing rule has earned nothing since.** SPY beats the rule on Sharpe in every window.

**Reading.** Across the two batches (Entries 79-80) every famous approach tested is either a drawdown/risk tool
(trend, risk parity, vol-managed), a thin crash-prone premium (carry), a decayed or local anomaly (Halloween), or a
null (pairs, vol breakout, short-vol timing since 2018). None beats a broad equity index on Sharpe, none is a
retail-accessible return source, and the funds' actual edge in these constructions is leverage and breadth.
Ledger: 6 more p-values registered (178 registered + 84 unregistered = m=262, Bonferroni 0.00019); the only rows
passing are still the three S&P index-gate drawdown effects. 219 tests pass (2 new). 69 -> 72 mechanisms tested;
nothing is declared tradable.


## Eighty-first: which sectors, factors and assets did better after past oil-shock regimes? A long-history (1947-2026) descriptive study — no timing tilt is statistically supported, the one mechanism-coherent tilt (energy equities) is a coin flip in the current episode's first quarter, and two results that looked significant were measurement artifacts

Requested direction: keep testing, analyse current geopolitical/market conditions, and find what worked in similar
political and financial eras. Entry 57 already tested analog matching (no skill) and described S&P/Sensex returns after
10 Brent-shock episodes 1989-2022; it never asked which SECTORS/FACTORS/ASSETS did well, and 2007+ NSE data holds almost
no oil-shock-plus-tightening episodes. `probe_regime_longhist.py` (pre-registered in its docstring before any return was
computed; independent code review BEFORE this write-up) uses monthly US data 1926-2026: Ken French factors and 48
industries, FRED WTI (1946+), GS10 and DGS10, and the datasets/gold-prices monthly file. Data cached in
`longhist_cache.csv` (French/FRED revise history; committed for reproducibility).

**Current conditions (2026-09-21).** Web-sourced, kept OUT of every test (aggregator text, some of it internally
inconsistent): Brent back above $100 in September after a ~20% jump on Middle East shipping risk, an ongoing Iran
conflict, a Fed hike to 3.75-4.00% in September with ECB (2.5%) and BoJ also tightening, US midterms ahead. From price
data (`probe_macro_analog.py --state-only`, 2026-09-18): NIFTY 23,346, -11% from its 252d high and below its SMA100/150/200
(gate RISK-OFF), Brent +36%/60d (96th pct), US 10y up (86th pct), India VIX 11.4 (5th pct: fear absent). NIFTY's nearest
2007+ analogs are poor (each differs by 0.7-1.2 sd on Brent) precisely because that sample contains no comparable shock.

**Regimes, at month-end t (data through t only; forward windows start t+1; declustered >= 12 months):**
A = WTI monthly average +30% over 3 months AND a 12-month high. B = A and GS10 +0.50pp over 6 months. A flags 12 completed
episodes 1948-2022 (1948-01, 74-01, 79-07, 89-01, 90-08, 94-06, 99-04, 2003-02, 04-10, 07-11, 2021-01, 22-03) plus the CURRENT
onset 2026-03; B flags 5 (79-10, 94-06, 99-04, 2021-02, 22-03). 2021-01/02 is the post-COVID oil rebound, not a supply
shock; the mechanical rule counts it and it was kept. B has only 4-5 events, so it is close to powerless. Statistic: mean
forward 3/6/12-month compounded return after events minus the same series' mean over all eligible months; two-sided p from
20,000 draws of the same number of eligible months under the same spacing (seeded). Registered: 9 series x 3 horizons x 2
regimes = 54 tests; plus a 288-cell industry scan (each of 48 industries minus the market).

**Results, regime A (12 complete episodes; forward return after events vs all months, 12m unless noted; p uncorrected).**
Market total return +9.5% vs +12.9% (p=0.46; positive in 9 of 12; the three losers are 1974, 2007-11 and 2022); size, value
and momentum factors: nothing (12m p 0.95/0.65/0.36); trend-gated market (10-month average): +10.7% vs +11.3% (p=0.86), i.e.
the same as holding; gold 6m +5.4pp (p=0.23), 12m p=0.66; 10y Treasuries 3m -0.6% vs +1.4% (p=0.079), 6m p=0.052, 12m
p=0.50; defense stocks vs market: -4.8pp (p=0.43); **energy stocks minus market: +8.5pp over 12m (p=0.073), positive in 8
of 12 episodes, median +6.7pp** (the four negatives are 1990, 1994, 1999, 2003, i.e. the years of transient spikes, a
post-hoc reading). Regime B (n=5): market, size, value, trend-gate all null; momentum +9.7pp/12m (p=0.105); energy vs market
+13.2pp/12m (p=0.092); bonds 3m -3.3pp (p=0.078); gold 3m +9.5pp (p=0.041) is ONE episode (1979-10, +69.6%; the other four
average -2.3%). **Industry scan: 20 of 288 cells under p<0.05 uncorrected vs ~14 expected by chance; best p=0.0011 (semis
after regime-B events, i.e. 1999 and 2021 tech booms coinciding with oil rebounds, not an oil channel); 0 pass the honest
family threshold.** 0 of the 54 registered p-values pass Bonferroni against m=616 (0.00008), or against the 54 alone
(0.00093).

**Current episode, descriptive (first 3 months after the 2026-03 flag, Apr-Jun 2026; n=1, no test):** market +15.1%, momentum
factor +16.2%, gold -13.7%, 10y bonds ~0, **energy equities -27.9% relative to the market.** Energy had already run
+40% relative to the market in Jan-Mar (before the mechanical flag) and gave it back in Apr-Jun while the broad market
rebounded 10%+5%. So in the one episode we are living through, the flag arrived AFTER producers had repriced, the opposite of
the 2004, 2007, 2021 and 2022 pattern. Not evidence against the tilt (3 months, one episode), but not confirmation either.

**Independent code review (4 findings, all valid, all fixed and rerun; two changed registered numbers).**
1. *BOND10 leaked in-flag-month drift.* GS10 is a monthly AVERAGE, so a "forward" return from the month-t average already
   contains yield movement inside month t, known at the flag date, and yields are trending by construction in regime B.
   This manufactured p=0.0095/0.0118 (B, 3m/6m) and 0.033 (A, 3m); with month-end DGS10 they are 0.078/0.090/0.079. I had
   noticed the averaging artifact and added a month-end side row, but the biased series was the one registered; now the
   registered BOND10 is month-end and the original is reported as unregistered `BOND10AVG`.
2. *The month-end bond series counted the still-forming September as a full month* (its n was 12, not 11); truncated to
   complete months.
3. *The gold file is a monthly average too* (correlation 0.995 with an average of COMEX closes, 0.59 with month-end closes;
   lag-1 autocorrelation +0.17 vs -0.08 for month-end returns). GOLD's forward window now skips month t+1 (`SKIP`); the
   unskipped version is unregistered `GOLDAVG`. Effect small (regime B 3m p 0.041 either way).
4. *The scan's Bonferroni threshold was hardcoded (m=604)*; now read from `multiple_comparisons.py` (m=616).
Also fixed before the review, by my own check: `trend_series` used a NaN-moving-average comparison that is False, not NaN, the
same bug class as Entry 76's gate; early months were silently "risk-off". An independent loop-based reimplementation
(no probe helpers) reproduced the event list and the market's 3m/12m numbers exactly. Lesson, now the fourth time: a
"monthly" series from FRED or a data-file that is really an average must never be the base of a forward return.

**Ledger.** 54 p-values registered; UNREGISTERED_SCAN_CELLS is now 84 + 288 + 12 = 384 (industry scan plus the 12 leaky
BOND10AVG/GOLDAVG rows); honest family m=616, Bonferroni threshold 0.00008. Consequence: the S&P 1950+ SMA100 gate row
(p=1e-4) no longer clears it; only the SMA150 and SMA200 rows (<=5e-5) do. 229 tests pass (10 new).

**Net verdict.** Descriptive and low-powered by construction (n=12 and n=5, permutation controls that cannot resolve
anything near a corrected threshold). What it says: after oil-shock regimes the broad market, size, value, momentum, gold and a
trend gate showed nothing reliably different from any other period; bonds tended to lose over the following 3-6 months but
not significantly on clean data; energy equities beat the market in most episodes (8 of 12) at a magnitude dominated by
five episodes, and the current episode's first quarter went the other way. **No strategy or tilt is supported; the count of
mechanisms stays 72 (this is a study of existing assets, not a new mechanism); nothing is declared tradable.** Untested and
not attempted: an India-only energy-versus-consumer tilt (n would be ~4 Brent shocks since 2007), using daily rather than
monthly data to time entries after the flag, and conditioning on the VIX regime (India VIX is at the 5th percentile now,
unlike every prior shock).


## Eighty-second: NSE delivery-percentage signals — abnormal delivery (D1) is the first signal in this daily-IC framework to replicate on two disjoint universes, but it is information without economics, and only partly independent of reversal and volume

Requested direction: keep testing new strategies. Every prior mechanism used prices, volume or option open interest; NSE also publishes, per stock per day, the share of traded quantity that was DELIVERED (`sec_bhavdata_full_DDMMYYYY.csv`), a positioning measure specific to India and never used here. `probe_delivery_signal.py`
(pre-registered in its docstring before any signal touched a return; independent code review BEFORE this write-up).
**Data limit, found first:** the file exists only from ~2019-10 (the older MTO archive 404s for every date tried), so ~1,723 trading days and no earlier decade. That fixed the design: a DAILY cross-sectional rank IC (~1,650 decisions x ~50 names) instead of a monthly rotation (~84 decisions), replicated on TWO DISJOINT universes (A = the 52-stock `WIDE_UNIVERSE`, B = the 54 different `UNIVERSE_B` names). Only delivery PERCENTAGES (ratios) are used, never raw delivered quantity (NSE quantities are not split-adjusted); returns are Yahoo adjusted closes.

**Signals (close of day t; direction fixed in advance as "high => higher future return"; lag-1 entry, h in {1, 5, 10, 21}):**
D1 = mean delivery % over t-4..t minus the mean over t-64..t-5; D2 = D1 x sign of the 5-day return (a surge on a rising vs a falling stock). Statistic: mean cross-sectional rank IC, p from 4,000 circular shifts of the signal panel (>= 60 days, (count+1)/(n+1); floor 0.00025). Registered: 2 x 4 x 2 = 16. **Decision rule (fixed in advance):** advance only if BOTH universes have IC > 0 with p < 0.05, both halves positive in both, AND the gross top-5 excess over h days exceeds the 0.25% round-trip cost.

**Results (mean IC, uncorrected p; A then B).** D1: h=1 +0.0165 (p=0.0010) / +0.0077 (p=0.049); h=5 +0.0180 (0.024) / +0.0177 (0.037); h=10 +0.0169 (0.080) / +0.0252 (0.023); h=21 +0.0080 (0.50) / +0.0131 (0.27). Both halves are positive at h=1 and h=5 in both universes (h=5: A +0.0200/+0.0160, B +0.0326/+0.0028). D2 is null (B h=21 is p=0.047 with a NEGATIVE IC, reported as reversed, not adopted). 6 of 16 cells are under p=0.05 (chance: 0.8), five of them D1 with positive IC. **Economics fail:** the gross excess of the five highest-D1 names over the universe mean is +0.02%/+0.04% over 1 day, +0.10%/+0.16% over 5 days, +0.16%/+0.33% over 10 days, against 0.25% round-trip cost. h=1 and h=5 pass the statistical parts of the rule and fail the cost gate; h=10 fails on p in A. **Verdict: information without economics.**

**Is D1 just reversal or volume?** Mean daily rank correlation of D1 with the intraday-move reversal signal (I5, Entry 84) is +0.16/+0.15, with 5-day reversal +0.19/+0.17, with abnormal volume (V1, Entry 83) -0.10/-0.04 (delivery share rises on QUIET days). A spanning check (`--spanning`, unregistered; per date, regress D1's ranks on those three and re-score the residual, raw and residual scored on the SAME cells): A h=1 IC 0.0166 -> 0.0141 (p 0.0025 -> 0.0055), h=5 0.0180 -> 0.0153 (p 0.020 -> 0.048); B h=1 0.0077 -> 0.0042 (p 0.043 -> 0.26), h=5 0.0177 -> 0.0141 (p 0.027 -> 0.059), h=10 0.0252 -> 0.0237 (p 0.0235 -> 0.033). **So roughly 75-85% of D1's IC survives removal of the known signals, but significance becomes borderline and B's 1-day effect disappears: partly, not fully, independent.** Correction to my own earlier statement in this session: the FIRST spanning run scored raw and residual on different days and looked stronger ("survives removal"); the review caught it and the corrected table above is the one to cite.
Head-to-head in the same window (Entry 83, P1): abnormal volume V1 has IC <= 0.015 with p >= 0.14 in both universes, so delivery is stronger than volume here (the difference is not itself tested).

**Independent code review (5 findings, all valid, verified against the data, fixed and rerun).** (1) One bad Yahoo day (2025-03-18: zero volume for most names) blanked the VOLUME signal for 65 days in both universes (~4% of P1, all in its second half); windows now tolerate missing days (5-day needs 4, 60-day needs 55), disclosed as a post-review data-quality correction, the pre-registered signals/horizons/direction/rule/null unchanged. (2) The spanning check compared different day sets (above). (3) The delivery cache's calendar was the master calendar: four special weekend sessions have no Yahoo bar, and the fetch had skipped weekends (missing the 2025-02-01 Budget Saturday) and swallowed an error for 2022-08-08; the calendar is now `^NSEI`'s, the fetch covers every day and prints non-404 errors. **Found while fixing it: NSE served a zipped .xlsx at the CSV URL for 2022-08-08, so that day has no delivery data at all** (recorded as missing; tolerated by the windows). (4) `ic_test`'s <200-day early return returned an unmasked IC series. (5) The three entries had near-copies of the loader and runner; one shared `load_fields`/`run_signals` now. Positive result of the refactor: Entry 84's 48 cells are bit-identical before and after. The review also confirmed no lookahead in any signal/forward alignment, correct `top_excess`, a valid null (the circular-shift null's SD is ~2x an iid null's, i.e. conservative), and no leak across the P1/P2 boundary.

**Ledger.** 16 registered (Entries 82-84 together add 96 registered rows and 30 unregistered robustness cells: spanning 8, the reversal positive control 16, the IBS-vs-I5 check 6). Honest family now 328 registered + 414 unregistered = m=742, Bonferroni threshold 0.00007. D1's best p (0.0010) clears the entry's own 16-test threshold (0.0031) but is 15x above the honest one; nothing in Entries 82-84 passes it, and the p-values at the 4,000-draw floor (0.00025) could not pass it at any rate. Caveats: one 7-year window including 2020; both universes are today's constituents; delivery share is also mechanically related to expiry days and index events; costs modelled at 0.125%/leg.

**Net verdict.** Delivery percentage carries a small, replicated, cross-sectional signal (IC ~0.017-0.025 at 1-10 days) that is partly independent of reversal and volume, and that is too small to trade: the top-5 edge is 0.10-0.16% per 5 days against 0.25% cost. Not built into a tracker (nothing to track at these economics). Untried and unlikely: a broader/slower basket dilutes the per-rebalance excess while the cost per rebalance stays. 73 mechanisms tested; nothing is declared tradable.


## Eighty-third: abnormal trading volume, 20 years, two universes, two independent periods — a clean null; and the known 5-day reversal, run through the same machinery, shows the test can see a real effect

`probe_volume_signal.py` (pre-registered; reviewed). The closest freely available relative of delivery with a long history, so it doubles as the control for Entry 82: is there a "high-volume return premium" (Gervais-Kaniel-Mingelgrin) on NSE, replicated across time and stocks? Yahoo adjusted close/volume, 20y, the same two universes, calendar `^NSEI` (never "the stock with the most rows"), P1 = 2019-10..2026-09, P2 = 2007-09..2019-09. V1 = ln(5-day mean volume / prior 60-day mean); V2 = V1 x sign(5-day return); high = long. Registered: 2 x 4 x 2 x 2 = 32. Same decision rule, and it requires BOTH periods and BOTH universes.

**Result: null.** Mean IC within +-0.015 in every cell and no positive-IC cell under p=0.05. Three cells are under 0.05 and all are NEGATIVE at h=1 (A P2 V2 p=0.019, B P2 V1 p=0.0145, B P2 V2 p=0.0012): a volume surge is followed by a slightly lower next day, IC -0.007 to -0.010, top-5 excess ~0; reported as reversed sign per the pre-registration, not adopted. Chance would give ~1.6 of 32. **Nothing advances.**

**The test can see a real effect (positive control, unregistered, same panels and null, 2,000 draws):** the known short-term reversal, signal = -(5-day return), h=5: earlier decade IC +0.0365 (A, p=0.0005) and +0.0291 (B, p=0.0005); recent period +0.0218 (B, p=0.0015) and +0.0154 (A, p=0.061). Signals with a mean IC of ~0.015-0.02 are detectable here, so the volume null is a real null, not an insensitive test. The 21-day reversal (`-ret21`) is not detectable at h=21 (p 0.33-0.55 in the 4 cells).

**Review finding that applies here:** the 2025-03-18 bad-volume day blanked V1 for 65 days (both universes), leaving P1 with 1,656 usable days instead of 1,721 and no signal in its second half's last stretch; fixed (see Entry 82). Corrected P1 results changed nothing (no cell crossed 0.05). 32 p-values registered. 74 mechanisms tested; nothing is declared tradable.


## Eighty-fourth: overnight vs intraday, gap reversal and MAX (lottery) — the known 5-day reversal lives in the INTRADAY part of the move, not in overnight gaps, and it is still below cost; overnight persistence and MAX are null

`probe_ohlc_signals.py` (pre-registered; reviewed). Yahoo adjusted open/close, 20y, the same two universes and periods. Signals (high = long): O20 = mean overnight return over 20 days (overnight persistence, Lou-Polk-Skouras); G5 = -mean overnight return over 5 days (gap reversal); I5 = -mean intraday (open-to-close) return over 5 days (intraday reversal); MAX = -max daily return over 21 days (lottery preference, Bali-Cakici-Whitelaw). h in {1, 5, 21}. G5 and I5 split the known 5-day reversal into its two halves. Registered: 4 x 3 x 2 x 2 = 48; same rule (both periods, both universes, halves, cost gate).

**Results.** **O20 and G5: null** in every cell (ICs ~0, mixed signs): overnight persistence does not exist here and gap reversal carries nothing. **I5 at h=5:** IC +0.0159 (A P1, p=0.0495), +0.0332 (A P2, p=0.0002), +0.0176 (B P1, p=0.019), +0.0295 (B P2, p=0.0002); halves positive in all four; at h=1 three of four cells are significant (A P1 p=0.13); at h=21 the recent-period cells are negative and null. **The economics fail the rule:** top-5 gross excess at h=5 is 0.08% (A P1), 0.20% (B P1), 0.38% (A P2), 0.24% (B P2) against 0.25% cost: only the survivorship-inflated earlier-decade cell clears it. **MAX:** significant with positive IC at h=1 in A P1 (p=0.045), A P2 (0.0085) and B P2 (0.0002) but B P1 p=0.82 and every top-5 excess ~0; h=5/21 null or reversed. **12 of 48 cells under 0.05 (chance ~2.4), all positive, all I5 (9) or MAX at h=1 (3); nothing advances.**

**Descriptive finding: the reversal is an intraday effect.** Stocks that fell (rose) between open and close over the last 5 days bounce (fade) afterwards; stocks that gapped overnight do not. Consistent with overnight moves carrying information and intraday moves carrying liquidity/noise, but that mechanism is not tested here. **Relation to IBS (this project's flagship signal):** mean daily rank correlation of I5 with -IBS(5) is +0.71 (A, 2019-26), so I5 is related but not the same. On the same daily all-days footing IBS(5) has IC +0.006 (p=0.46) at h=5 vs I5's +0.016 (p=0.06): IBS's daily IC over 2019-26 is ~0, which fits Entry 64's finding that its edge sits only in month-end entries (one universe, one period, 2,000 draws; a cross-check, not a test).

**Ledger.** 48 registered; 12 of 48 under 0.05 is the largest excess over chance in Entries 82-84, and it is one known effect (reversal) decomposed, not a new one. The four earlier-decade I5 cells are also the ones a loser-bounce effect is most inflated in by today's-constituents survivorship. 75 mechanisms tested; nothing is declared tradable.


## Eighty-fifth: stock-futures open-interest "buildup" and options put/call signals — a clean null, 0 of 64 cells clear even an uncorrected p<0.05

Continuing the daily cross-sectional rank-IC framework validated in Entries 82-84, on a signal
dimension never touched here: LEVERAGED derivative positioning rather than price, volume, or
delivery. "Long/short buildup" (price and open interest moving together) is one of the most
popular retail signals in Indian trading commentary and had never been tested in this project.
Scope was widened from futures-only to include stock OPTIONS put/call ratios before any data was
fetched or scored (the same F&O bhavcopy files hold both, at no extra fetch cost).

`probe_oi_signal.py` (pre-registered in its own docstring before any signal was scored; independent
review already applied to its shared `ds` = `probe_delivery_signal` helpers). Data: NSE F&O bhavcopy,
2016-01 through today (2,639 trading days), FUTSTK/OPTSTK (old format) and STF/STO (new format) rows,
summed over all listed expiries/strikes per symbol per day, on the same two disjoint universes (A:
`WIDE_UNIVERSE`, 46-52 names with futures; B: `UNIVERSE_B`, 54) and two periods (P1 = 2019-10+, P2 =
2016-2019-09, an independent EARLIER period this project's delivery data can't reach since NSE's
delivery archive only goes back to 2019-10) as Entries 82-84. Signals at close t (direction fixed in
advance, high => higher future return, the retail "bullish buildup" reading): OI1 = 5-day OI change
normalized by its own 60-day mean (positioning build, side unknown); OI2 = OI1 restricted to the
OI-up quadrants x sign of the 5-day return (the classic long/short-buildup reading; the original
implementation, which also scored OI-down days with the opposite sign to the retail "short
covering" reading, is kept as unregistered `OI2X` — a review finding disclosed in the probe's own
docstring, before any cell was scored); PC1 = -ln(put OI / call OI) (informed-options-trading
direction, per Pan-Poteshman); PC2 = the 5-day change in that ratio. Forward returns lag 1 (enter
next close), h in {1, 5, 10, 21}. Registered: 4 signals x 4 h x 2 universes x 2 periods = 64 tests.

**Persistence correction, disclosed before scoring (the probe's own "PRE-SCORING AMENDMENTS"):** OI
levels and put/call ratios persist day to day (unlike delivery %, which mean-reverts), so the
circular-shift null used everywhere in Entries 82-84 is not centred at zero here. Every cell
therefore reports both the shift p and a Newey-West t-test p (lag h+5) plus `null_z0` (how far off
centre the shift null sat — up to 0.9 sd for `PC1`), and **the registered p is `max(p_shift,
p_nw)`**: a cell counts as a candidate only if BOTH tests clear 0.05, not either one alone. This
matters concretely: several cells (e.g. `PC1 h=10/21 univ B`, shift p=0.058/0.053) sit just under an
uncorrected 0.05 on the shift test alone and fail once the NW test is required too (p_nw=0.042/0.028
— actually lower there, but the max rule still requires both, and other cells flip the other way).

**A real bug, found and fixed before any result could be trusted (not a pre-registration
amendment — a runtime crash in the unregistered-robustness code path).** The unregistered
expiry-window variants (`OI1E`/`OI2E`/`PC2E`, which null out the 5-day window whenever it straddles
a monthly options/futures expiry — OI and put/call ratios collapse ~85-90% at expiry, and that roll
is a persistent per-name fixed effect the shift null doesn't control) crashed with `ValueError: Array
conditional must be same shape as self`: `keep = ~roll.reindex(...).to_numpy()[:, None]` produced a
`(T, 1)` numpy array, and pandas' `DataFrame.where()` requires an array-like conditional of the
EXACT same shape as the frame it's applied to — unlike numpy, it does not broadcast a `(T, 1)` array
against a `(T, N)` frame. Fixed by passing the boolean Series directly with `.where(keep, axis=0)`,
which is pandas' own supported broadcasting path for a row-wise mask. This blocked the entire probe
from completing (`main()` computes registered and unregistered signals together, so the whole run
failed) — every number below is from the fixed script, verified against 9 unit tests
(`tests/test_probe_oi_signal.py`) that were passing against the isolated function calls even while
`main()`'s end-to-end path was broken.

**Results: a clean null, cleaner than chance predicts.** 0 of 64 registered cells clear
max(p_shift, p_nw) < 0.05 (uncorrected chance alone would produce ~3.2). The decision rule (fixed in
advance: BOTH universes AND BOTH periods need IC > 0 with p < 0.05 under both tests, both halves
positive, and the top-5 gross excess exceeding the 0.25% round-trip cost) finds nothing advancing.
Unregistered robustness (`OI2X`, `OI1E`, `OI2E`, `PC2E`, 64 more cells, expiry-window-excluded and the
original OI2 implementation): 2 of 64 under the max rule (`OI2X h=5 univ A P1`, `PC2E h=5 univ B P1`,
both p_nw just under 0.05, neither replicating across period or universe) — also no advance. Data
audit before scoring, per the probe's own disclosure: 2,639 of 2,641 `^NSEI` days present in the
cache, no duplicates, no NaN totals; one day (2021-03-30) is a genuine NSE 404, tolerated by the
4-of-5/55-of-60 windows.

**Ledger.** 64 registered p-values added (honest family: 408 registered + 478 unregistered scan
cells — including these 64 unregistered OI robustness cells — = m=886, Bonferroni threshold
0.00006). Nothing here comes close to either the entry's own 64-test threshold (0.00078) or the
honest one. 76 mechanisms tested; IBS rotation remains the sole standing finding; nothing is
declared tradable.


## Eighty-sixth: dividend month premium and trailing dividend yield — the first fundamentals-adjacent signal tested here, and a clean null

This project has never had fundamentals data, so no value or yield factor was testable until Yahoo's
per-share dividend histories (available from ~1999-2002, 30-40 payments per name in this universe)
made two classics testable for the first time: Hartzmark & Solomon (2013), "The dividend month
premium" — stocks earn abnormal returns in the calendar months they're PREDICTED to pay a dividend
(predicted from the same month a year earlier), a demand/price-pressure effect needing no
fundamentals — and trailing dividend yield as the simplest quality-free value proxy (high yield =>
higher return). Unlike the daily families of Entries 82-85, these are MONTHLY decisions (~100-130
per period), so the pre-registration is tighter: 16 tests, not 64.

`probe_dividend_signal.py` (pre-registered; independent review already applied via the shared `ds`
helpers). Data: Yahoo `history(period="max", auto_adjust=False)` per stock — `Adj Close`
(dividend-and-split-adjusted, total return) for returns, `Close` (split- but not dividend-adjusted,
same basis as the raw `Dividends` column) for yield, ex-dividend dates from `Dividends`. Same two
universes as every other Entry-82-onward probe. Decision dates are month-ends; the incomplete current
month has no forward return and drops out. Signals (direction fixed in advance, high => higher
future return): `DIV1` = 1 if the stock went ex-dividend in the calendar month twelve months before
the forward month (Hartzmark-Solomon's own predictor), h=1 only (a month-of-payment effect, not a
multi-horizon one); `DYLD` = trailing-12-month dividends / close at decision, h in {1, 3, 12} months.
Registered: (DIV1 x 1 + DYLD x 3) x 2 universes x 2 periods = 16.

**Persistence correction, disclosed before scoring (mirrors Entry 85's, independently discovered
first — the OI probe's own docstring credits this entry's review for the pattern):** `DYLD`'s yield
ranks have ~0.8 twelve-month autocorrelation, so a circular-shifted copy is nearly the same ranking —
the shift null sat 1.9-2.4 sd off centre (`null_z0`), making its two-sided shift p meaningless. For
`DYLD` the registered p is therefore the Newey-West t-test of the mean IC (lag = h) instead; `DIV1`'s
null is centred (`null_z0` = -0.26 to -0.46 sd across cells) so it keeps the pre-registered shift p.
Both p's are reported per cell (`p_basis` column records which was used); `dividend_signal_results.csv`
already carries the basis-selected value in its own `p` column, used directly here rather than
recomputed.

**Results: null, one lone significant cell that fails the pre-registered rule outright.** 15 of 16
cells have p >= 0.15; the exception is `DIV1 P2 univ B` (IC=+0.0357, p=0.0157, the earlier/pre-2016
period on the 54-stock universe) — but the decision rule requires BOTH universes AND both periods to
clear p<0.05 together, and `DIV1 P2 univ A` sits at p=0.1475 (same period, different universe,
opposite-enough to fail the pairing outright), so no signal advances. `DYLD` (the "simplest
quality-free value factor") shows no edge at any horizon in either universe or period — the highest
IC magnitude is +0.0426 (`DYLD h=12 univ A P2`) at p=0.52. Gross excess (unregistered, not tested):
mixed sign, mostly small, consistent with the null read.

**Ledger.** 16 registered p-values added (no new unregistered scan cells — every signal/cell computed
here was individually registered). Honest family: 408 registered + 478 unregistered = m=886 (shared
with Entry 85's addition above), Bonferroni threshold 0.00006; nothing here is within two orders of
magnitude of it. 77 mechanisms tested; IBS rotation remains the sole standing finding; nothing is
declared tradable.


## Eighty-seventh: realized-skewness cross-sectional rotation — a real academic factor, genuinely different from everything tried so far, and a clean null that gets worse under stress

Continuing the "keep testing" direction with a signal dimension none of the 77 prior mechanisms
touched: the SHAPE of the recent return distribution, not its level (momentum, reversal), its
range (IBS, MAX), its volume, its open interest, or its dividends. Amaya, Christoffersen, Jacobs &
Vasquez (2015), "Does Realized Skewness Predict the Cross-Section of Equity Returns?" (Journal of
Financial Economics): stocks with LOW (more negative) realized skewness of daily returns over the
recent past earn HIGHER subsequent returns — a real, cited, out-of-sample-replicated academic
finding, distinct from lottery-preference/MAX (Entry 84's daily MAX signal is the single most
extreme observation in a window; skewness is the third moment of the WHOLE distribution, a
different statistic entirely, and no realized-skewness signal has been computed anywhere in this
project before).

Implemented by adding one new `kind == "skew"` branch to `probe_reversal_rotation.py`'s existing
`scores()` dispatcher, reusing every other piece of that file's now-mature rotation machinery
unchanged: `simulate()`'s lag-1 fill (the look-ahead fix Entry 59 already applied to every other
rotation in this family), the same 52-stock `WIDE_UNIVERSE`, the same STT+stamp+DP cost model, and
the same random-portfolio significance control used for reversal/momentum/52-week-high. No new
probe file, no new scaffolding — this is the smallest possible addition that gets a brand-new
signal the full rigor of the established framework for free. Score = rolling `window`-day skewness
of daily simple returns (`Series.rolling(window).skew()`, pandas' own adjusted Fisher-Pearson
estimator — no new dependency); ascending sort already picks the lowest (most negative) skew first,
matching the paper's own predicted direction, so no negation is needed (unlike `hi52`/`mom` in the
same file). Pre-registered before any cell was scored: `window` in {21, 63} trading days (the two
lookbacks most commonly used for "realized" skewness in this literature — roughly one and three
months) x `top_k` in {3, 5, 8} (this project's own standard three sizes) = 6 tests. Independent
`/code-review` of the diff run before this write-up (medium effort): no findings — additive-only,
argument order at the new call site correct, no re-implementation of existing helpers.

**Result: a clean null across all 6 cells, every one worse than its own random-portfolio
control.** skew(21): top_k=3 10.07%/yr (maxDD 46.1%, random mean 13.83%/yr, p=0.6696); top_k=5
11.38%/yr (DD 39.5%, random 13.61%/yr, p=0.6376); top_k=8 10.15%/yr (DD 40.2%, random 13.21%/yr,
p=0.8035). skew(63): top_k=3 16.90%/yr (DD 28.5%, random 15.31%/yr, p=0.3031, the closest of the
six and still nowhere near 0.05); top_k=5 11.87%/yr (DD 31.9%, random 14.72%/yr, p=0.7035); top_k=8
7.96%/yr (DD 36.0%, random 14.53%/yr, p=0.9680). Every cell's own random control OUTPERFORMS it on
average — the opposite of a promising signal — and both walk-forward halves are positive in every
cell only because the whole 2016-2026 window is a rising market for nearly any long-only monthly
basket, the same caveat this project has applied to every other basket-wide bet since the
Cross-mechanism synthesis entry.

**Survivorship stress (the Fortieth entry's four real blowups added, 56 stocks; same 6 cells,
400-seed control, not separately pre-registered — a due-diligence rerun, not a second family):
every cell gets WORSE, not better.** skew(21) top_k=3 falls to 6.15%/yr (DD 53.9%, p=0.7681);
top_k=5 to 9.83%/yr (DD 46.7%, p=0.5885); top_k=8 to 9.05%/yr (DD 37.3%, p=0.7032). skew(63) falls
hardest: top_k=3 6.88%/yr (DD 69.2%, p=0.7756), top_k=5 3.21%/yr (DD 60.7%, p=0.9776), top_k=8
2.11%/yr (DD 59.5%, p=0.9950). This is the opposite of IBS rotation's own stress result (Fortieth
entry: IBS's headline number went UP under the identical stress, because IBS harvests a one-month
bounce after a crash rather than holding through it). Realized skewness's own mechanism plausibly
explains the difference: a stock genuinely mid-collapse (JETAIRWAYS, YESBANK, RCOM, PCJEWELLER)
shows persistently negative rolling skewness for MONTHS, not just the single bounce-window IBS
catches, so a low-skew rotation is more likely to hold a real falling knife through its next leg
down rather than catching its bounce — a plausible mechanism, not separately tested here, since the
signal already fails cleanly without needing this explanation to reject it.

**Ledger.** 6 registered p-values added (honest family: 414 registered + 484 unregistered scan
cells — including the 6 stress-test reruns, counted as scan cells per this project's standing
convention rather than a second pre-registered family — = m=898, Bonferroni threshold 0.00006);
nothing here is within an order of magnitude of either the entry's own 6-test threshold (0.0083) or
the honest one. 78 mechanisms tested; IBS rotation remains the sole standing finding; nothing is
declared tradable.


## Eighty-eighth: Stochastic Oscillator (%K) mean reversion — 0/12, the same clean washout as Bollinger Bands, and a real same-bar reentry bug caught before any number was trusted

Sourced from the plain, textbook version of one of the oldest and most widely published technical
indicators (George Lane, 1950s-70s; every major charting platform ships the identical formula, so
unlike Squeeze/SuperTrend/volume there is no single reference implementation to cite). Genuinely
different construction from every mean-reversion strategy already tried: IBS is a same-day
positional read of today's own high-low range; RSI(2) smooths up/down move magnitudes; Bollinger
Bands is a statistical band on the close series. Stochastic %K instead bands today's close within
the highest-high/lowest-low RANGE of the last `k_period` days — a different normalization from all
three, effectively "IBS computed over a 14-day window instead of one day," directly testing whether
IBS's own short-horizon edge (this project's flagship, but thin as a single-instrument signal —
Entry 13 found only one gold survivor at a 12.5% hit rate) generalizes to a longer lookback.

Implemented as `probe_stochastic.py`, standalone (same shape-mismatch reasoning as IBS/SuperTrend/
gap-fill: `check_entry(close)` only receives today's close, not today's own high/low, which %K's
window needs). Rule: long when %K(14) < `entry_threshold` (20, the standard oversold line), exit
when %K climbs back >= `exit_threshold` (80) or `max_hold_days` (20) times out; short side (%K >
100-entry_threshold) is this project's own symmetric extension, unconfirmed by literature, same
caveat already attached to IBS's/volume's/52-week-high's. ATR-based stop (`stop_atr_multiple` x
ATR, 2.0 default), the same convention RSI-2/Squeeze/Bollinger/MACD use for a signal with no
natural structural stop. Classic implementations also smooth %K into a %D signal line and trade
the crossover; this probe deliberately uses the plainer %K-only threshold instead, matching this
project's established "try the simplest textbook rule first" choice for RSI-2/Bollinger/IBS.

**A real bug, caught by an independent `/code-review` before any number was trusted (not a
pre-registration disclosure — a runtime correctness defect in the first draft).** Two findings that
mattered: (1) the exit check and the entry check were two independent `if`s rather than mutually
exclusive, so a long reverting at %K>=80 could immediately reopen as a short on the SAME bar (which
also satisfies %K>80 when entry_threshold=20) — fixed to the same no-same-bar-reentry convention
`probe_ibs.py` already uses (`elif` on the original position state). (2) the stop-loss check
compared only the day's CLOSE against the stop, silently missing a genuine intrabar breach that
recovered by the close — fixed to check the day's high/low instead, the same convention
`backtest_daily.py`'s engine already documents for exactly this reason (understating drawdown and
overstating returns otherwise). A third finding (an unused %D computation, dead weight with no
effect on behavior) was also removed rather than left half-wired. All three fixed before any
backtest number below was generated; a synthetic sanity check (flat-then-gap-down price series,
not a pytest file — this project's standalone probes don't get one, per established convention)
confirmed %K reads exactly as expected (50 while flat mid-range, 8.33 once gapped near the window
low) both before and after the fix.

**Screening result (the same 12-instrument set used throughout Entries 12-21, `--walk-forward`,
default params): 0/12 pass** (both halves positive) — the same clean washout Bollinger Bands
produced in the Twenty-seventh entry (also 0/12), the worst hit rate tier in this project alongside
it. 6 of 12 are consistent LOSERS, both halves negative (`^NSEBANK`, `RELIANCE.NS`, `TCS.NS`,
`SBIN.NS`, `WIPRO.NS`, `CL=F`); the other 6 sign-flip between halves (`^NSEI`, `INFY.NS`,
`HDFCBANK.NS`, `ITC.NS`, `AXISBANK.NS`, `GC=F`). No single-instrument survivor to chase, so no
perturbation/quarter-split/sizing check was run — the same "how uniform the failure is" reasoning
the Bollinger Bands entry already used to skip those steps on an equally clean 0/12.

**Net verdict.** The answer to "does IBS's short-horizon edge generalize to a 14-day range
normalization" is no, at least not via the plain %K threshold rule tested here — consistent with
this project's own Forty-third entry (the cross-sectional-rotation recipe doesn't generalize past
mean-reversion signals with a genuinely short/same-day character) and its Twenty-seventh/
Twenty-eighth entries (Bollinger Bands' pure band-touch mean reversion, with or without a trend
filter, also failed cleanly). 79 mechanisms tested; IBS rotation remains the sole standing finding;
nothing is declared tradable.


## Eighty-ninth: Parabolic SAR — this project's first always-in-market strategy, an 8% hit rate (the lowest yet), and one lone survivor with real internals but thin, sizing-limited magnitude

Wilder's Parabolic SAR ("New Concepts in Technical Trading Systems", 1978 — the same book RSI and
ATR, both already used throughout this project, come from). Genuinely different trend-following
construction from Donchian (a fixed N-day channel) and SuperTrend (an ATR-multiple band that
ratchets but never accelerates): SAR's acceleration factor grows every time price makes a new
extreme in the trend's direction, so the stop tightens progressively as a trend matures — "the
parabola catches up to price." It is also the first ALWAYS-IN-MARKET strategy tried anywhere in
this project: every other strategy here has flat periods between signals; a stop-and-reverse system
has no flat state by construction.

Implemented as `probe_parabolic_sar.py`, standalone (same shape-mismatch reasoning as every other
full-OHLC-dependent probe here). Wilder's own recurrence: `SAR_i = SAR_{i-1} + AF*(EP_{i-1} -
SAR_{i-1})`, with the no-penetration rule (SAR never set past the prior two bars' low/high in an
uptrend/downtrend) and the classic 0.02/0.02/0.20 start/step/max acceleration-factor triple. On a
reversal, SAR resets to the abandoned extreme point and AF resets to `start_af`. Bootstrap (no
natural "day 0" state exists for a stop-and-reverse system) is disclosed rather than hidden: initial
trend from a simple `close_1 >= close_0` test — NOT the "which side of a wide band is price already
on" heuristic the SuperTrend entry found was a near-always-true tautology, since a one-day close
comparison carries no such structural bias. A synthetic sanity check (a monotonic uptrend, then a
sharp reversal) confirmed SAR tracks correctly below/above price, AF accelerates on new extremes,
and a reversal resets both SAR and AF exactly as Wilder's algorithm specifies, before any real
backtest number was generated.

**A real position-sizing bug, caught by an independent `/code-review` before any number was
trusted.** SAR is *designed* to converge on price as AF accelerates — unlike every ATR-scaled stop
already used in this project (RSI-2/Squeeze/Bollinger/MACD/SuperTrend all size off a stop that stays
roughly `multiplier x ATR` away from price), SAR's own stop distance can legitimately shrink toward
zero in a mature trend. The first draft sized `qty = risk_amount / |close - stop|` with no floor, so
a near-zero stop distance could blow qty up to an arbitrary multiple of the intended
`risk_per_trade_pct` — exactly the situation the Twenty-third entry's `volatility_position_size()`
(Carver-style ATR cap) already exists to catch, just never wired into this specific probe. Fixed by
capping `qty` at `min(stop_based_qty, risk_amount / ATR)`, the same `min()`-of-two-sizing-methods
convention that entry established. **This materially changed which instrument survives**: before
the fix, `WIPRO.NS` was the lone passer; after it, `WIPRO.NS` flips to INCONSISTENT and `GC=F` (gold)
becomes the lone passer instead — a concrete demonstration of why this bug mattered, not just a
theoretical concern.

**Screening result (the same 12-instrument set used throughout Entries 12-21, `--walk-forward`,
default params, POST-fix): 1/12 pass** (both halves positive) — `GC=F` only, an 8.3% hit rate, the
LOWEST of any strategy tried in this project (below Bollinger's/Stochastic's 0/12 only in the sense
that at least one instrument passed here at all, but below every other strategy's nonzero hit rate:
MACD 16.7%, sector sweep 18%, Squeeze/Turtle Soup 25%, volume 30%, SuperTrend 33%). 4 of 12 are
consistent losers (`^NSEI`, `INFY.NS`, `HDFCBANK.NS`, `ITC.NS`, `AXISBANK.NS` — both halves
negative), the rest sign-flip between halves.

**`GC=F`'s internals are genuinely better than the hit rate alone suggests, unlike most lone
survivors in this project's history.** Quarter-split: +3.05% / -2.09% / +0.29% / **+2.68%** — 3 of 4
positive, and Q4 (2024-2026, the most recent and most relevant window) is positive and relatively
strong, the "single-instrument, Q4-favorable" signature the Cross-mechanism synthesis entry
associates with real (if thin) effects, not the "historic run now flat" decay pattern that
disqualified several other lone survivors (Donchian/BTC, momentum rotation, low-volatility).
Perturbation on the AF triple (0.01/0.015/0.02/0.03/0.04, step=start in each case): 4 of 5 pass
(only the tightest, 0.01, flips inconsistent) — no single-point-fit at the exact default, unlike
`AXISBANK.NS`'s SuperTrend `st_period` sweep or the regime gate's `vol_period` sweep.

**But sizing doesn't help past the default, closing off the one lever that turned several other
thin survivors into something more substantial (3-bar breakout, Squeeze, SuperTrend, Turtle Soup,
52-week-high all had this lever; RSI-2 and volume-CMF+OBV's `GC=F` survivor did not).** At the
default 0.5% risk-per-trade: 1.32%/year at 9.3% max drawdown (206 trades, no halt) — thin, in the
same bucket as CMF+OBV's own `GC=F` survivor (1.30%/year, Sixteenth entry) and RSI-2's verdict. At
1% risk it drawdown-halts (10.3% DD, trade count collapsing 206->51); at 2% it also halts, despite a
higher raw annualized figure (3.37%/year on far fewer completed trades before the halt) — the
"dilutes/halts rather than compounds" shape, not the clean scaling 3-bar breakout or SuperTrend's
oil survivor showed.

**Net verdict.** Eighty-ninth mechanism, and — like IBS's original single-instrument gold survivor
(Thirteenth entry) and SuperTrend's oil survivor before its own retest disproved it (Seventeenth/
Eighteenth entries) — a real research trail with a credible mechanism (Wilder's own accelerating
trailing stop, correctly implemented and sanity-checked) that produced exactly one instrument
clearing the bar, at the lowest hit rate of any strategy tried here. The internals (quarter-split,
perturbation) look cleaner than most lone survivors' do, but per this project's own established
standard (see the SuperTrend/Eighteenth entries' own retest precedent), a single-instrument survivor
from a below-chance-level sweep isn't independently confirmed until retested against more
instruments of a similar kind (gold's own instrument class — MCX commodities/FX — the same class
`CL=F`'s SuperTrend survivor was retested against and failed 0/8 on). Not done in this entry —
flagged, not claimed as found, the same treatment IBS's and SuperTrend's lone survivors received
while still unconfirmed. 80 mechanisms tested; IBS rotation remains the sole standing finding;
nothing is declared tradable.


## Ninetieth: DMI/ADX (Wilder's directional-movement/trend-strength system) — a chance-level 2/12 hit rate, but the survivor that matters clears every check, and gold keeps reappearing as a survivor across unrelated mechanisms

The last major indicator from Wilder's own 1978 book ("New Concepts in Technical Trading Systems")
not yet tried in this project (RSI, ATR, and Parabolic SAR are already here). Genuinely different
mechanic from every trend-follower tried so far: Donchian is a fixed N-day channel, SuperTrend an
ATR-multiple ratchet band, Parabolic SAR an accelerating trailing stop — none of them separate trend
STRENGTH from trend DIRECTION. DMI/ADX does: +DI/-DI measure which direction is winning (derived
from how much of today's high/low move is a genuinely new directional extreme, via Wilder's own
+DM/-DM/TR construction), and ADX measures how strongly EITHER direction is winning, independent of
which one — a trend-strength FILTER layered on top of a directional signal, a shape nothing else
here has.

Implemented as `probe_dmi_adx.py`, standalone (same full-OHLC dependency as SuperTrend/SAR/
Stochastic). Smoothing uses this project's own established plain-rolling-average convention (not
Wilder's exponential smoothing) — the same deliberate choice already made for SuperTrend's ATR,
disclosed rather than silently substituted. Rule (the simplest, level-based version, matching this
project's now-standard "try the plain textbook rule first" convention): long when ADX >=
`adx_threshold` (25, Wilder's own "trending market" line) AND +DI > -DI; exit on the opposite
crossover, ADX falling back under the threshold, or `max_hold_days` timeout. Short side is this
project's own symmetric extension, unconfirmed by literature, the same caveat attached to every
other short side added here. ATR-based stop, capped by the Twenty-third entry's own Carver-style
ATR sizing from the start (not retrofitted after a review caught it missing, as happened with
Parabolic SAR) — though, as the independent review below noted, that cap is mathematically inert
here whenever `stop_atr_multiple >= 1` (the default is 2.0), since an ATR-scaled stop already makes
`risk_amount/risk_per_unit < risk_amount/atr` by construction — exactly the "already redundant by
construction" finding the Twenty-third entry itself reached for every other ATR-scaled-stop
strategy in this project. Two synthetic sanity checks (a strong monotonic uptrend, and a choppy
flat-drift series) confirmed +DI dominates with ADX=100 in the first case and ADX stays under 25 in
the second, before any real backtest number was trusted. Independent `/code-review` (medium) before
this write-up: no findings — correctly guards against every bug class this project's three most
recent probes were each caught on (same-bar reentry, close-only stop checks, unfloored sizing).

**Screening result (the same 12-instrument set, `--walk-forward`, default params): 2/12 pass**
(`WIPRO.NS`, `GC=F`) — 16.7%, matching MACD's own hit rate exactly, at the low end of this project's
established chance-level band (sector sweep 18%, MACD 16.7%). 3 of 12 are consistent losers
(`INFY.NS`, `HDFCBANK.NS`, `AXISBANK.NS`); the other 7 sign-flip between halves.

**`WIPRO.NS` fails on the very next check.** Quarter-split: Q1 -0.23% / Q2 +4.05% / Q3 +2.76% /
**Q4 -2.31%** — 2 of 4 quarters negative, and Q4 (2024-2026, the most recent and most
relevant window) is one of them — the recent-quarter-decay signature this project has repeatedly
used to disqualify a coarse-screen "passer" (Donchian/BTC, momentum rotation, low-volatility, IBS's
52-week-high survivors). Dropped without a perturbation sweep, per this project's own established
"stop at the first clear crack" practice.

**`GC=F` (gold) clears every subsequent check.** Quarter-split: +1.38% / +2.09% / -1.21% / **+1.28%**
— 3 of 4 positive, and Q4 is positive, the "single-instrument, Q4-favorable" signature this project
associates with real (if thin) effects rather than decay. Perturbation: `adx_threshold`
(15/20/25/30 all pass, only the wide 35 fails — 4/5) and `dmi_period`/`adx_period` (10/14/28 pass,
only 20 dips inconsistent — 3/4) together clear 7/9 cells, with no cliff sitting at the exact
default in either dimension (unlike the single-point-fit red flags this project has flagged
elsewhere — `AXISBANK.NS`'s SuperTrend `st_period`, the regime gate's `vol_period`). **Sizing
genuinely helps, not dilutes**: 0.5% risk gives 0.95%/year (4.1% max DD, 142 trades); 1% risk gives
**2.14%/year (7.5% DD)** — a real ~2.25x scaling on the same trade count, not the "return and
drawdown dilute together" pattern RSI-2/volume-CMF/Parabolic SAR's own `GC=F` survivor all showed;
2% breaks it (drawdown-halted at 11.8% DD, trade count collapsing 142->99) — 1% is near the safe
ceiling, not a floor to push past, the same shape every other "sizing helps" candidate in this
project has had.

**Worth naming explicitly: gold has now turned up as a survivor across FOUR separate, unrelated
mechanisms in this project** — IBS (Thirteenth entry), CMF+OBV volume confirmation (Sixteenth),
Parabolic SAR (Eighty-ninth), and now DMI/ADX. None of the four mechanisms shares a construction
with any other (same-day range position, volume-direction confirmation, an accelerating trailing
stop, and now a directional-strength filter), which weakens (though doesn't eliminate) the "one
lucky instrument from a chance-level sweep" explanation each individual entry has had to apply on
its own — four independent chance-level sweeps landing on the SAME instrument is itself a mild
positive signal about gold specifically, worth flagging as a pattern even though no single one of
the four clears this project's bar alone, and even though the capital-tier investigation (Twenty-
fifth/Twenty-sixth/Twenty-ninth entries) already found gold's real MCX contract sizes block every
one of these at this project's target capital regardless of whether the underlying signal is real.

**Net verdict.** Ninetieth mechanism, and — like the Parabolic SAR entry immediately before it — a
below-chance-level hit rate (16.7%) with one survivor whose internals (quarter-split, perturbation,
AND sizing) are cleaner than most lone survivors in this project's history, landing closer to
Turtle Soup's `AXISBANK.NS` or SuperTrend's original `CL=F` reading than to a thin, no-lever
"real-but-negligible" result. Per this project's own established standard (the SuperTrend/
Eighteenth entries' retest precedent), still not independently confirmed — a single-instrument
survivor from a chance-level sweep needs retesting against more instruments of a similar kind before
being called found, and the accumulating gold-across-mechanisms pattern above is exactly the kind of
cross-check that retest should specifically account for rather than treating each entry's gold
survivor as an isolated coincidence. Not done in this entry. Also separately blocked by the
capital-tier wall already established for gold at this project's target scale, independent of
whether the signal itself is eventually confirmed. 81 mechanisms tested; IBS rotation remains the
sole standing finding; nothing is declared tradable.


## Ninety-first: retesting DMI/ADX's gold survivor against 8 more commodities/FX pairs — 0/8, the exact same fate SuperTrend's oil survivor met, closing the loop the prior entry flagged

Direct follow-up on the Ninetieth entry's own flagged next step, no new code: `probe_dmi_adx.py`
(unchanged) run against the same 8-instrument commodity/FX extension set this project has used twice
before for exactly this kind of retest (the Eighteenth entry's SuperTrend follow-up, the
Twenty-fifth's IBS/FX follow-up) — `NG=F`/`HG=F`/`SI=F`/`PL=F` on MCX-equivalent futures, `USDINR=X`/
`EURINR=X`/`GBPINR=X`/`JPYINR=X` on the currency segment — specifically because gold is itself a
commodity and this is the natural instrument class to look for corroboration in, the same reasoning
the Eighteenth entry used for oil.

**Result: 0/8 pass** (both walk-forward halves positive). 3 of 8 are consistent losers on both
halves (`NG=F`, `SI=F`, `PL=F` — `SI=F`/`PL=F` also drawdown-halted out-of-sample); the other 5
sign-flip between halves (`HG=F`, `USDINR=X`, `EURINR=X`, `GBPINR=X`, `JPYINR=X`). Not one cell comes
close to `GC=F`'s own clean quarter-split/perturbation/sizing profile.

**This is the identical outcome SuperTrend's `CL=F` survivor met under the same retest (Eighteenth
entry: 0/8) and IBS's own FX retest met before the lot-size problem even had to be invoked
(Twenty-fifth entry's headline numbers were later retracted on capital-tier grounds, but the
underlying FX signal itself was never independently corroborated across the set either).** `GC=F`
isn't corroborated by nearby instruments the way a real cross-instrument commodity/FX mechanism
would be — it was the one lucky draw the Ninetieth entry's own 16.7% chance-level screen already
implied might exist, same as `CL=F` was for SuperTrend.

**Read together with the Ninetieth entry's "gold across four mechanisms" observation, this
sharpens rather than erases it.** The pattern isn't "gold generalizes across the commodity/FX class"
(this retest rules that out cleanly, the same way the Eighteenth entry ruled it out for oil) — it's
specifically instrument-level: something about GOLD ITSELF, not commodities/FX broadly, keeps
producing a lone survivor across unrelated technical constructions (same-day range position, volume
confirmation, an accelerating trailing stop, a directional-strength filter), while the immediately
neighboring instruments in its own asset class consistently don't. That's still not evidence any
one of those four mechanisms is individually real (a chance-level sweep landing on the same
instrument four times could itself be a property of gold's own return distribution — e.g. its
historically lower volatility/cleaner trending character relative to silver/platinum/FX pairs
inflating walk-forward pass rates generically, not a signal-specific effect) — a question this
entry doesn't resolve and flags rather than chases further, since answering it would need a test
of "does ANY simple trend/mean-reversion rule pass more often on gold than on a matched-volatility
random-walk control," a different and larger undertaking than a single-mechanism retest.

**Net verdict.** No count change (not a new mechanism — a retest of the Ninetieth entry's own
finding, same convention as the Eighteenth/Twenty-fifth entries' own retests). Closes the DMI/ADX
line the same way SuperTrend's oil line was closed: real, sanity-checked, correctly-implemented
code; one clean-looking survivor; zero corroboration from the instrument class it belongs to. 81
mechanisms tested; IBS rotation remains the sole standing finding; nothing is declared tradable.


## Ninety-second: the day-of-week effect — the classic Monday effect doesn't replicate on NIFTY, a "Wednesday effect" shows up independently on both markets, and neither clears cost or a corrected bar

The oldest, simplest calendar anomaly this project had never directly tested: French (1980),
"Stock returns and the weekend effect" — average Monday returns historically lower, often negative,
than other weekdays. Genuinely different from the Sixth entry's turn-of-month rule (a multi-day
window keyed to the calendar MONTH): this is a single-day classification keyed to the calendar
WEEK, no lookback or lookahead of any kind — about as simple a signal as exists, and one this
project somehow never tried despite testing turn-of-month, overnight/intraday decomposition, and
VIX-regime timing in the same entry.

`probe_day_of_week.py`: mean daily return by weekday (`Mon`-`Fri`) on two independent, long-history
markets — `^NSEI` (NIFTY, 20y, 4,665 trading days) and `^GSPC` (S&P 500, full Yahoo history, 24,797
trading days, ~98 years) — tested against a 5,000-draw random-same-size-subset-of-all-trading-days
null (the same "random subset" control-family shape used throughout this project's cross-sectional
rotation entries, applied here to a random subset of DAYS instead of stocks). Pre-registered: 5
weekdays x 2 markets = 10 tests, direction NOT fixed to French's own "Monday worst" prediction,
since other researchers have repeatedly found the classic effect weakening or reversing in more
recent decades — reported regardless of sign. A synthetic sanity check (a known +1% bump injected
into every 5th synthetic "Monday") confirmed the random-subset control correctly detects a real
effect (p<0.001) before any real data was scored. Independent `/code-review` (medium) before this
write-up: no findings.

**Results: the classic Monday effect does NOT replicate on NIFTY, and a different day — Wednesday —
shows up as significant, independently, on BOTH markets.**

| market | day | mean/day | p |
|---|---|---|---|
| NIFTY | Mon | +0.0063% | 0.935 |
| NIFTY | **Wed** | **+0.1098%** | **0.039** |
| NIFTY | Thu | -0.0202% | 0.776 |
| S&P 500 | **Mon** | **-0.0618%** | **0.025** |
| S&P 500 | **Wed** | **+0.0775%** | **0.0008** |

(Tue/Fri on both markets, and the two rows above with no p shown, are all p >= 0.08, omitted for
space.) The classic literature's own predicted direction (negative Monday) shows up on the S&P
(p=0.025) but is essentially null on NIFTY (p=0.935, mean near zero) — the same "real in the US,
absent in India" pattern already found for the Halloween effect (Seventy-ninth entry). The
Wednesday result is the more interesting one precisely because it wasn't the hypothesis motivating
this entry: positive and individually significant on BOTH markets, with both halves of each
market's own sample positive too (NIFTY 0.131%/0.089%, S&P 0.085%/0.070% — no decay, no sign flip).

**But it doesn't clear either a corrected significance bar or realistic transaction costs.** Within
this entry's own pre-registered 10-test family (Bonferroni threshold 0.005), only the S&P's Wednesday
cell (p=0.0008) survives; NIFTY's Wednesday (0.039) and the S&P's Monday (0.025) do not. Against the
honest project-wide family (now m=908, threshold 0.00006 after registering all 10 cells here),
nothing comes remotely close. **Economically, a single-day-hold weekly trade washes out the gross
edge entirely**: NIFTY's +0.1098%/day gross is below this project's own established 0.25%
round-trip NSE cost threshold (the same bar Entries 82-86's daily cross-sectional signals were held
to) — net roughly -0.14%/trade; the S&P's +0.0775%/day gross against this project's own
~0.1%-round-trip US-equity convention (Seventy-ninth entry) nets to roughly -0.02%/trade,
essentially a wash. This is the same "real gross pattern, unharvestable at the frequency required to
capture it" lesson the Sixth/Thirty-seventh entries already established for the overnight-drift
anomaly, now shown at weekly rather than daily frequency.

**Net verdict.** Ninety-second mechanism, and a clean example of this project's own standard
methodology doing its job: a cross-market-replicated pattern (Wednesday, on two markets
independently) that still fails once correction and realistic costs are both applied, while the
textbook hypothesis that motivated the test (Monday) fails to replicate outside the market it was
originally documented in. Not pursued further — a slower, lower-frequency construction (e.g. a
monthly rebalance conditioned on that week's expected weekday composition) would dilute the signal
faster than it cuts cost, the same arithmetic that closed the overnight-drift line. 82 mechanisms
tested; IBS rotation remains the sole standing finding; nothing is declared tradable.


## Ninety-third: the gold-shuffle-control — a scrambled-time test of whether gold's own return-shape drives its repeat survivorship, not genuine temporal structure

Direct follow-up on the open question the Ninetieth and Ninety-first entries named but didn't
chase: whether gold's repeat appearance as a lone survivor across four unrelated technical
mechanisms (IBS - Thirteenth; CMF+OBV volume confirmation - Sixteenth; Parabolic SAR -
Eighty-ninth; DMI/ADX - Ninetieth) reflects genuine, if elusive, temporal structure in gold's price
series, or is instead a property of gold's own volatility/return-distribution (lower realized
volatility meaning fewer drawdown-halts, mechanically raising the odds of a "both walk-forward
halves positive" screen passing, independent of real temporal order).

**Method** (`probe_gold_shuffle_control.py`, found uncommitted from a prior session; traced by hand
for correctness before trusting any output, not run through `/code-review` — specifically checked
that each synthetic day's open/high/low/close is the SAME real day's own gap/range ratios
re-applied to a new running close, so `high >= open,close` and `low <= open,close` stay intact
under the scaling, and that the walk-forward split-by-position logic in all four downstream
mechanisms doesn't depend on real calendar order, only list position, so relabeling shuffled bars
with their original position's date is safe): a day-level block bootstrap — record each real
trading day's (open, high, low, close) as ratios of the PRECEDING day's close, plus that day's
volume, a "shape" tuple capturing that day's own gap and intraday range with no reference to
calendar time; shuffle the ORDER of the 2,513 shape tuples (day 0's real close is the fixed
starting point) and re-multiply them out into a synthetic series that reproduces gold's exact
marginal gap/range/volume distribution while destroying any genuine serial dependency (trend
persistence, mean reversion, autocorrelation) a real rule would need to find. 200 shuffles, `GC=F`,
freshly fetched 10y history (2,514 trading days) — the same random-subset/shuffle control-family
shape used throughout this project (Thirty-ninth entry onward), applied here to the order of TIME
on one instrument instead of to which stocks get picked each month. Each of the four already-
tested, already-reviewed mechanisms' own `walk_forward` function is reused UNCHANGED against both
the real series and every shuffle — no strategy logic reimplemented; "pass" = both walk-forward
halves net-positive, this project's own standing screening bar throughout. Per the script's own
pre-registered caveat (checked via a synthetic check before trusting anything below): injecting a
genuine, strong regime-switching signal into synthetic data still produced a real-vs-scrambled gap
too noisy to trust from a single draw, so the comparison this entry actually relies on is
base-rate matching — whether gold's own scrambled pass rate sits close to this project's
established chance-level range across many instruments/strategies (16-33%), not whether real gold
"beats" its own scrambled distribution in one draw.

**First finding, before the control even mattered: IBS no longer survives on a fresh pull.** Real
`GC=F`: `ibs=False, sar=True, dmi_adx=True, cmf_obv=True` — only three of the original four
mechanisms still pass on this snapshot. This is this project's own documented rolling-window drift
(flagged as an open risk in earlier entries, never directly caught mid-effect before) actually
erasing one of the four "gold survivors" the Ninetieth entry's framing was built on — the
four-mechanism premise is down to three before the shuffle control says anything at all.

**For the three that still pass, the scrambled-time pass rate splits, not uniformly:**

| mechanism | real result | scrambled pass rate (200 shuffles) |
|---|---|---|
| SAR | pass | 14.0% |
| DMI/ADX | pass | 17.0% |
| CMF/OBV | pass | 45.0% |

SAR's 14% sits at/just below this project's established chance-level range; DMI/ADX's 17% sits
squarely inside it — neither shows gold's own shape as unusually easy to pass on for these two
mechanisms, so their one real pass each reads as an ordinary, unremarkable base-rate event, not
evidence gold's distribution is doing the work (though the reverse — genuine structure — isn't
confirmed by this either; absence of inflated false-positive rate is an absence of disconfirming
evidence, not positive evidence). **CMF/OBV's 45% is a different story** — roughly 1.4-2.8x this
project's own established chance ceiling, the clearest single number this project has produced yet
for the "gold's own properties, not real structure" explanation. That mechanism's gold
survivorship (Sixteenth entry) now looks like a distributional artifact, not a found edge.

**Net synthesis: mixed, not a clean resolution either way.** One of the original four "gold
survivors" didn't even reproduce on a fresh data pull; one of the remaining three (CMF/OBV) is now
flagged as a likely distributional artifact by its own pre-registered methodology; the other two
(SAR, DMI/ADX) pass a test that fails to debunk them but doesn't confirm genuine structure either.
Read against the Ninety-first entry's own closing question, this narrows rather than resolves it:
gold isn't uniformly "just an easy instrument to pass screens on" (SAR/DMI-ADX don't support that),
but it also isn't uniformly "hiding real structure four different rules each find" (CMF/OBV's
number argues against that for at least one of the four, and IBS's non-replication undercuts the
four-mechanism framing itself).

**Net verdict.** Not a new mechanism — a diagnostic follow-up on the Ninetieth/Ninety-first
entries' own flagged question, same convention as the Ninety-first entry's own retest. 82
mechanisms tested; IBS rotation remains the sole standing finding; nothing is declared tradable.


## Ninety-fourth: astrology as a strategy — the lunar-phase effect (Yuan, Zheng & Zhu 2006) replicated as directionally consistent but not significant on either market

Requested direction: add astrology and aesthetics as strategy inputs and check them. Astrology's
most defensible, actually-published form in finance is the lunar-phase effect: Yuan, Zheng & Zhu
(2006), "Are investors moonstruck? Lunar phases and stock returns" (Journal of Empirical Finance),
found returns around new moon reliably higher than around full moon across 48 countries, with
investor mood/sleep-disruption floated as the behavioral channel. Genuinely different from every
calendar effect already tested here (day-of-week, turn-of-month, Halloween, January): those key off
the CALENDAR; this keys off an independent astronomical cycle (the 29.53-day synodic month) that
drifts relative to weekdays and months, so it can't be a repackaging of an already-tested effect.

`probe_lunar_cycle.py` (new; pre-registered in its own docstring before any return was computed,
same convention as the Ninety-second entry): moon phase computed from days-since-a-known-reference-
new-moon (2000-01-06) modulo the synodic month (29.530588853 days) — no ephemeris library added,
plain stdlib math, calendar-day granularity (a few hours' error against the true instant of each
phase, immaterial at daily-return resolution). Each trading day classified "new"/"full" (within ±3
days of the respective phase) or "other"; tested on NIFTY (20y) and the S&P 500 (full Yahoo
history, ~98y), the same random-same-size-subset null as the Ninety-second entry, direction not
fixed (reported regardless of sign, though the literature's own predicted direction — new > full —
is one specific, named cell, not assumed).

**Results:**

| market | bucket | n | mean/day | p |
|---|---|---|---|---|
| NIFTY | new | 933 | +0.0749% | 0.197 |
| NIFTY | full | 916 | +0.0038% | 0.956 |
| NIFTY | other | 2,817 | +0.0460% | 0.438 |
| S&P 500 | new | 5,056 | +0.0531% | 0.080 |
| S&P 500 | full | 4,994 | +0.0319% | 0.495 |
| S&P 500 | other | 14,748 | +0.0241% | 0.881 |

No cell clears even uncorrected p<0.05 on either market. **But the direction is consistent with the
published effect on both, independently:** new-moon mean return exceeds full-moon mean return on
NIFTY (+0.0711%/day) and the S&P (+0.0212%/day) — the same sign the literature predicts, on two
unrelated markets (a coin flip on each independently, so a 25% chance of both landing this way by
pure luck) — mildly interesting but nowhere near a claim of significance, and the S&P's "new" cell
(p=0.080) is the closest any lunar cell gets to conventional significance, still failing it.

**Net verdict.** A real, published, peer-reviewed anomaly, correctly implemented and directionally
replicated on two markets, that does not clear even the weakest (uncorrected) bar here. 6 p-values
registered (honest family: 908 -> 914, Bonferroni threshold unchanged at 0.00006 since nothing here
is within two orders of magnitude of it). 83 mechanisms tested; IBS rotation remains the sole
standing finding; nothing is declared tradable.


## Ninety-fifth: aesthetics as a strategy — round-number ("psychological barrier") price-level proximity, a clean null on both markets

Companion to the Ninety-fourth entry's astrology test, same requested direction. The most
defensible "aesthetics" hypothesis in market microstructure is the round-number/psychological-
barrier literature (Sonnemans 2006, "Price clustering and natural resistance points in the Dutch
stock market"; Bhattacharya, Holden & Jacobsen 2012, "Penny Wise, Dollar Foolish") — traders find
round price levels more salient/aesthetically preferable as reference points and cluster limit
orders there, which the literature ties to altered short-horizon return behavior near those levels
independent of any real economic information. This project has never tested a pure PRICE-LEVEL
signal before (every prior mechanism keyed off returns, volume, open interest, delivery or the
calendar).

`probe_round_number.py` (new; pre-registered): for each trading day, distance from that day's close
to the nearest round level (a multiple of `--round-step`) expressed as a fraction of the step (0 =
sitting on the level, 0.5 = exactly between two); "near" if that fraction is <= 0.10, else "far".
`round_step` = 1,000 points for NIFTY, 500 for the S&P (index-appropriate round increments, not
tuned to the data). Tested: next trading day's return conditional on near/far, same random-subset
null, direction not assumed (a round number could plausibly act as a magnet or a barrier).

**Results:**

| market | bucket | n | mean/day | p |
|---|---|---|---|---|
| NIFTY | near (20.5% of days) | 955 | +0.0548% | 0.388 |
| NIFTY | far | 3,711 | +0.0405% | 0.631 |
| S&P 500 | near (38.3% of days) | 9,490 | +0.0230% | 0.820 |
| S&P 500 | far | 15,308 | +0.0370% | 0.172 |

Clean null on both markets, no cell near uncorrected p<0.05, and the sign isn't even consistent
across markets (near > far on NIFTY by +0.0142%/day, near < far on the S&P by -0.0140%/day) —
unlike the lunar entry's cross-market-consistent direction, this one doesn't agree with itself on
sign.

**One descriptive aside, not tested further here:** the S&P's "near" share (38.3%) is much higher
than NIFTY's (20.5%) despite an identical `--near-frac` definition — plausibly because a FIXED
500-point step is a very different fraction of the index level across the S&P's ~76-year, ~60x
price range (roughly 100 in the 1950s to 6,000+ now) than across NIFTY's shorter, narrower-range
history, which would inflate or deflate the "near" share mechanically as the index re-scales,
independent of any real clustering behavior. Worth a relative (percentage-of-price) round-step
definition if this line is ever revisited — not built here (ponytail: flat point-based round-step
only; a %-of-price version is the upgrade path if this cell ever shows something worth chasing,
which it currently doesn't).

**Net verdict.** Aesthetics, in its most literature-grounded form (round-number salience), produces
no detectable next-day return effect on either market at daily resolution. 4 p-values registered
(honest family: 914 -> 918, Bonferroni threshold unchanged). 84 mechanisms tested; IBS rotation
remains the sole standing finding; nothing is declared tradable.


## Ninety-sixth: Mercury retrograde — real orbital mechanics, no library, direction matches folklore on both markets, nowhere near significant

Requested direction: keep pushing on astrology, specifically ANCIENT astrology — Mercury
retrograde is the oldest and most-cited example, going back to Ptolemy's Tetrabiblos (2nd century
CE), and the one folk-financial-astrology claim still actively circulated today (retail-trading
forums warning against opening new positions during a retrograde window). Different in kind from
the Ninety-fourth entry's lunar phase (a fixed 29.53-day cycle): retrograde windows are irregular
(~3-4 times a year, ~3 weeks each) because they come from the real relative geometry of two
elliptical orbits, not a simple period — this can't be approximated the way lunar phase was.

**No ephemeris library added** (checked first: none installed; ladder rung 5 doesn't apply).
`probe_mercury_retrograde.py` computes Mercury's geocentric ecliptic longitude from the standard,
public-domain, low-precision Keplerian orbital elements for Mercury and Earth (Standish/JPL,
"Keplerian Elements for Approximate Positions of the Major Planets", valid 1800-2050 AD,
~1-arcminute accuracy) — Kepler's equation solved by Newton's method, heliocentric orbital-plane
coordinates rotated into the J2000 ecliptic frame, Mercury's position minus Earth's gives the
geocentric longitude; a day is "retrograde" if that longitude moved backward (shortest-path
unwrapped) from the prior calendar day. **Self-check before trusting any market number** (ladder:
non-trivial logic gets one runnable check): over 2020-2026 the formula finds 22 retrograde episodes
(3.1/yr) averaging 22.4 calendar days — matching the well-documented real-world figures (~3-4/yr,
~21 days) closely enough to trust the day-level classification for a week-scale window, though not
for precision astrometry.

Pre-registered, same convention as the Ninety-fourth entry: mean daily return on retrograde vs
direct (non-retrograde) days, NIFTY (20y) and the S&P 500 (~98y), the same random-same-size-subset
null used throughout this project's calendar-effect entries. Folklore's predicted direction
(retrograde = worse) is named, not assumed; reported regardless of sign.

**Results:**

| market | bucket | n | mean/day | p |
|---|---|---|---|---|
| NIFTY | retrograde | 904 | +0.0273% | 0.698 |
| NIFTY | direct | 3,762 | +0.0474% | 0.340 |
| S&P 500 | retrograde | 4,748 | +0.0262% | 0.645 |
| S&P 500 | direct | 20,050 | +0.0329% | 0.360 |

No cell within striking distance of uncorrected p<0.05. **The sign matches folklore's prediction on
both markets independently** (retrograde mean is lower than direct: -0.0200%/day on NIFTY,
-0.0068%/day on the S&P) — the same "consistent direction, nowhere near significant" shape as the
Ninety-fourth entry's lunar result, and, same caveat as that entry, only a 25% base-rate coincidence
on its own if there's truly nothing there.

**Net verdict.** Real orbital mechanics, genuinely different construction from the lunar-phase
entry, self-checked against known real-world retrograde frequency/duration before any return was
trusted, direction consistent with 2,000-year-old folklore on two independent markets, magnitude
indistinguishable from noise. 4 p-values registered (honest family: 918 -> 922, Bonferroni threshold
unchanged). 85 mechanisms tested; IBS rotation remains the sole standing finding; nothing is
declared tradable.


## Ninety-seventh: astrology used AS A STRATEGY — a fixed common entry-timing rule scores 5/12 on this project's own screening bar, but every passer's magnitude is near zero

Requested direction: use astrology, not just measure it. The Ninety-fourth/Ninety-sixth entries
only tested whether returns DIFFER on astrologically-labelled days — descriptive, not a tradeable
rule. `probe_astrology_strategy.py` takes folklore's own stated advice literally and turns it into
an actual entry/exit state machine, reusing the Ninety-fourth/Ninety-sixth entries' own already
self-checked signal code UNCHANGED (`moon_phase`/`classify` from `probe_lunar_cycle.py`,
`retrograde_flags` from `probe_mercury_retrograde.py` — no new astronomical code): long-only, enter
when flat and BOTH astrologically-favorable conditions hold at once (moon in its "new" window AND
Mercury not retrograde), exit on Mercury turning retrograde, the moon reaching "full," an ATR stop,
or `max_hold_days` (30 — one lunar month, the signal's own natural timescale, not fit to data)
timing out. Same ATR-stop/risk-per-trade sizing convention every strategy here uses. Run on this
project's standard 12-instrument screening set (the same set DMI/ADX, Parabolic SAR and SuperTrend
were screened against: `INFY.NS`, `TCS.NS`, `HDFCBANK.NS`, `SBIN.NS`, `CL=F`, `GC=F`, `^NSEI`,
`AXISBANK.NS`, `ITC.NS`, `^NSEBANK`, `RELIANCE.NS`, `WIPRO.NS`), walk-forward, "pass" = both halves
net-positive with no drawdown-halt.

**Screening result: 5/12 instruments passed** — `INFY.NS`, `SBIN.NS`, `CL=F`, `ITC.NS`, `WIPRO.NS`.
41.7%, above this project's established chance-level range (16-33% across most prior technical
screens) and in the same territory as the 3-bar breakout's 50% (its best hit rate to date).

**But the magnitude kills it before any further check matters.** Every passer's annualized return,
both halves: `INFY.NS` +0.32%/+0.28%, `SBIN.NS` +0.04%/+0.34%, `CL=F` +0.35%/+0.56%, `ITC.NS`
+0.21%/+0.57%, `WIPRO.NS` +0.06%/+0.03% — ten numbers, none above 0.6%/yr, several near zero,
already net of commission. Compare to IBS rotation, this project's one real finding, at ~20%/yr:
these are 30-60x smaller, on a single undiversified instrument (not even portfolio-scaled). A
pass/fail count built almost entirely of trades this close to breakeven is a coin flip dressed as a
hit rate, not evidence of edge.

**A caveat specific to this entry, not present in prior technical-indicator screens: entry timing
is IDENTICAL across all 12 instruments** (moon phase and Mercury's position don't depend on which
stock you're looking at) — only the stop-hit path and the actual price move during each ~50-trade
set of shared calendar windows differ by instrument. That makes "5/12 independent replications" a
weaker form of corroboration than DMI/ADX's or SAR's per-instrument-idiosyncratic signals: it's
closer to one shared trade-timing pattern scored against 12 different price paths (a
cross-sectional event study) than 12 truly independent mechanism tests. Not fatal on its own (the
magnitude finding already closes this line regardless) but worth naming so a future entry doesn't
cite "5/12" as if it were the same kind of evidence as DMI/ADX's "2/12."

**Net verdict.** Astrology, turned into an actual rule and run through this project's real
screening bar, clears the hit-rate count but fails the economic bar every other survivor here has
had to clear (Parabolic SAR's thin-but-real magnitude, DMI/ADX's 2.14%/yr) by more than an order of
magnitude — closer to noise trading at cost than a found edge. Not pursued further (quarter-split/
perturbation checks would only be worth running on a result with real magnitude to begin with). 86
mechanisms tested; IBS rotation remains the sole standing finding; nothing is declared tradable.


## Ninety-eighth: the pre-holiday effect (Lakonishok & Smidt 1988; Ariel 1990) — the cleanest statistical hit since the day-of-week entry's Wednesday result, decaying over time and failing cost on NIFTY, thin but real on the S&P

Requested direction: keep searching for genuinely new mechanisms. The pre-holiday effect — returns
on the trading day immediately before an exchange holiday run unusually high, one of the oldest
documented calendar anomalies (Lakonishok & Smidt 1988, "Are Seasonal Anomalies Real?"; Ariel 1990,
"High Stock Returns before Holidays") — has never been tested here, and is genuinely different in
construction from every calendar effect already tried: day-of-week (Ninety-second entry) and
turn-of-month (Sixth entry) key off a FIXED calendar position; this keys off an IRREGULAR event (an
exchange closure) that can fall on any weekday.

**No external holiday calendar, no new dependency** (`probe_holiday_effect.py`): a day is classified
"pre-holiday"/"post-holiday" purely from GAPS in the trading-day sequence the data already
contains — for trading day t, the normal gap to the next trading day is 1 calendar day (Mon-Thu) or
3 (Friday), and a larger actual gap means a holiday fell in between. This generalizes correctly to a
holiday on any weekday, including ones adjacent to a weekend, and needs nothing beyond the OHLC data
already fetched.

Pre-registered, same convention as the Ninety-second entry: mean return on pre-/post-holiday days vs
baseline ("other"), NIFTY (20y) and the S&P 500 (~98y), the same random-subset null used throughout,
literature's predicted direction (pre > other) named, not assumed.

**Results:**

| market | bucket | n | mean/day | halves | p |
|---|---|---|---|---|---|
| NIFTY | pre | 280 | +0.1901% | +0.2770% / +0.1032% | 0.0252 |
| NIFTY | post | 280 | +0.1779% | +0.4134% / -0.0575% | 0.0388 |
| NIFTY | other | 4,120 | +0.0280% | +0.0069% / +0.0491% | 0.988 |
| S&P 500 | pre | 942 | +0.2520% | +0.3534% / +0.1506% | **0.0002** |
| S&P 500 | post | 943 | -0.0429% | -0.0932% / +0.0072% | 0.417 |
| S&P 500 | other | 22,915 | +0.0254% | +0.0103% / +0.0406% | 0.998 |

**The S&P result is this project's best calendar-effect p-value yet** — 0.0002, beating the
Ninety-second entry's Wednesday cell (0.0008) — magnitude (0.2520%/day, ~9.9x the "other" baseline
of 0.0254%) matching the classic literature's own oft-cited "several times the normal day" figure
almost exactly, and critically the pattern is EXACTLY what the literature specifically predicts:
pre-holiday elevated, post-holiday NOT (p=0.417, consistent with zero) — a real discriminating
test, not "anything unusual around a gap looks different."

**NIFTY's pre-holiday result replicates the same direction** (p=0.0252) but its post-holiday cell is
also significant (p=0.0388) — NOT predicted by the literature, and, checked against the halves, NOT
robust: NIFTY post-holiday flips sign between halves (+0.4134% then -0.0575%), the same
"front-loaded, not really there" pattern this project has flagged repeatedly elsewhere. Read as
likely noise, not a second real effect.

**Both markets' PRE-holiday effect decays across halves but does not flip sign** — NIFTY 0.2770% ->
0.1032%, S&P 0.3534% -> 0.1506% — matching later academic follow-ups (e.g. Marquering, Nisser &
Valla (2006), "Disappearing Anomalies," which specifically documents the pre-holiday effect
weakening after the 1987 crash) rather than contradicting them.

**Economics, using this project's own established cost conventions** (0.25% NSE round trip, Entry
73; ~0.1% US-equity round trip, Entry 79) — a trade held only the pre-holiday day itself, scored at
the RECENT (second) half's rate since that's what matters going forward: NIFTY nets 0.1032% - 0.25%
= **-0.15%/trade, a loser** at ~14 trades/year despite being statistically real. S&P nets 0.1506% -
0.10% = **+0.05%/trade**, thin but positive, at ~9-10 trades/year (roughly 0.5%/yr
gross-of-compounding) — the first calendar-timing effect in this project's recent entries to clear
its own cost bar on any market, even if barely.

**Against the honest multiple-comparisons family:** 6 p-values registered here. S&P's pre-holiday
cell (p=0.0002) is still ~3.7x above the Bonferroni threshold at m=922+6=928 (0.05/928 ≈ 0.000054)
— fails the corrected bar, same fate as every other finding in this project.

**Net verdict.** The most statistically convincing calendar result since day-of-week's Wednesday
cell, discriminates correctly between pre- and post-holiday (unlike NIFTY's spurious post cell),
decays honestly rather than flipping, and is the first calendar-timing effect here to clear a
realistic cost bar on any market (S&P, thinly). Doesn't clear the honest corrected significance bar.
Not built into a tracker (9-10 trades/year at a ~0.05%/trade net edge is too thin and infrequent to
justify the operational overhead of a live paper-tracker at this project's stage) — flagged as the
most promising untradable-yet finding since IBS rotation itself, worth a follow-up if this project
ever widens beyond NIFTY/S&P index-level testing to real tradable index products. 87 mechanisms
tested; IBS rotation remains the sole standing finding; nothing is declared tradable.


## Ninety-ninth: cross-sectional return seasonality (Heston & Sadka 2008) — a clean null; the design can only see effects of IC ~0.05+, and a ledger gap in Entries 94-98 was found and fixed

Requested: Entry 99. Picked a published cross-sectional factor never tested here and distinct from the dividend month premium (Entry 86, a 0/1 flag): a stock's return in a calendar month predicts that stock's return in the same calendar month of later years (annual lags 12, 24, ... months), other lags nothing. Ranking stocks against each other cancels any market-wide month effect. `probe_seasonality_signal.py`, pre-registered in its docstring before any signal was scored; independent `/code-review` (medium) before this write-up.

**Design.** SEAS1 = same-calendar-month return one year ago; SEAS3 / SEAS5 = mean of the last 3 / 5 annual lags; all data through the decision month-end only, a missing component makes the name NaN. Forward return lag-1 (enter the close after the decision month-end, exit the next month-end), the Entry 86 convention. Same two disjoint universes (A 52, B 54), P1 = decisions from 2016-01, P2 = before. IC test with the circular-shift null EXCLUDING whole-year shifts (`ds.ic_test(avoid_mod=12)`): the retained shifts pair a stock's history of month m with the return of month m+k, k != 0 mod 12, which is Heston-Sadka's own "other lags" control. Registered p = max(p_shift, p_nw); 3 x 2 x 2 = 12 tests; decision rule via `ds.apply_rule` (both universes AND both periods, both halves, gross top-5 excess > 0.25% cost). Stated up front: minimum detectable effect ~2.8 x SE(mean IC); the shift p cannot go below ~0.01 with ~100-150 months (only ~100 distinct non-whole-year shifts), so the Newey-West p resolves anything smaller (planted-IC unit test: p_shift 0.011, p_nw < 1e-6).

**Result: null.** 0 of the 10 testable cells is under p = 0.05 (smallest 0.058: B/P2/SEAS1; then 0.067: B/P1/SEAS5). Mean IC ranges -0.010 to +0.040 against a standard error of 0.016-0.027, i.e. this design could only see an IC of roughly 0.045-0.075, larger than the ~0.01-0.03 the US literature implies. So the null means "no effect large enough to see", not "no effect". Halves by date are mixed in sign; universe A shows nothing anywhere (P1 IC -0.010/+0.001/+0.012). The decision rule: stat_pass 0/4 for every signal, advance False for all three. The two SEAS5 P2 cells have only 40 valid months (< the 60 floor) and are untestable.

**A pre-registration error, disclosed rather than hidden.** The docstring said the `period="max"` calendar reaches back to 1997; Yahoo's `^NSEI` history begins 2007-09-17, the same as `20y`, so nothing was gained and SEAS5's P2 cells were never testable. The original wording is left in the file with a dated POST-RUN CORRECTION note beside it. Extending the sample with a stock-date calendar was considered and NOT done: both universes' P1 cells already fail the rule, so no P2 result could change the verdict.

**Post-hoc observation, not tested (added after the first run; no p-value).** The top-5 names by signal earned a mean monthly excess of +0.25% over the universe and the bottom-5 -0.10%; the tail spread is positive in 10 of 12 cells (mean about +0.35%/month) while the rank IC is about zero. My first guess, that the top-5 "gross excess" was just a volatility artifact (both tails hold the extreme names), is only half right: the bottom tail does NOT earn the same. So there may be a tail-only pattern that a rank IC dilutes across 50 names. The 12 cells overlap heavily (same months, three nested signals, so nowhere near 12 independent results), the long-only top tail nets ~0.25% against a 0.25% cost, and the short side is not implementable, so this is a hypothesis for a pre-registered tail test in a later entry, not a result. Not chased here.

**Review (3 findings).** (1) No exception handling around the Yahoo fetch: FIXED (retry, then loud exclusion). (2) `load_adj` near-copies `probe_dividend_signal.load_stock_panels`: documented debt, not merged (that function hard-codes a 20y calendar and also fetches dividends). (3) The docstring keeps the false "1997+" statement beside its correction, and the reviewer said this inflates the registered family by 2: declined, keeping a visible, dated correction is the disclosure convention here, and only 10 rows were registered (the two untestable cells have no p-value).

**Ledger gap found while registering (pre-existing).** `multiple_comparisons.py` held 424 rows while README and Entries 94-98 said 444 and m=928: the 20 p-values of Entries 94 (lunar, 6), 95 (round number, 4), 96 (Mercury, 4) and 98 (pre-holiday, 6) were described as "registered" but never added (Entry 97 has no p-value). Added them from the numbers printed in this file, plus this entry's 10. Family now 454 registered + 484 unregistered = m=938, Bonferroni threshold 0.000053; the same two S&P index-gate drawdown rows pass and nothing else does (the S&P pre-holiday cell, p=0.0002, is still about 4x above it). README updated (88 mechanisms, 454, m=938).

Tests: 5 new in `tests/test_probe_seasonality_signal.py` (signal = the same calendar month one year earlier; SEAS3/5 lags and required history; NaN not zero through the lag chain; no price after month-end d reaches the signal at d; a planted annual seasonality is detected and a null panel is not). 265 pass. Caveats: today's constituents (survivorship, worse early), Yahoo's pre-2010 NSE history is less reliable, 64-127 months per cell. **88 mechanisms tested; IBS rotation remains the sole standing finding; nothing is declared tradable.**


## Hundredth: NSE Bulk Deals / Block Deals "follow the smart money" — a clean null across all 16 registered cells, and a real data-source discovery arc before any signal touched a return

An explicitly "genuinely new" direction, never touched in 99 prior entries: NSE separately publishes two disclosure-driven reports built from price, volume, options OI, delivery, fundamentals or the calendar (Entries 1-99 cover all of those) but never from a single client's own disclosed large trade. BULK DEALS (any client trading >= 0.5% of a company's listed shares in one day) and BLOCK DEALS (a single trade >= 5,00,000 shares or >= Rs 5 crore, in the special block window) are the retail "follow the smart/large money" thesis this project had never actually tested with real data. `probe_bulk_deals_signal.py`, pre-registered in its own docstring before any signal was scored; independent `/code-review` before this write-up (8 findings, 6 fixed here, 2 accepted as low-priority/documented debt — see below).

**Data-source discovery, itself worth recording.** `https://www.nseindia.com/api/historicalOR/bulk-block-short-deals` is the only endpoint that serves this HISTORICALLY (the project's existing no-auth `nsearchives.nseindia.com` CSV archive that feeds delivery/OI/F&O only ever serves the CURRENT day for this specific report, confirmed by a live probe before building anything) — session-cookie-gated (a plain `requests.Session()` warmed by one GET to the report page, not the daily 2FA login dance Kite needs). **Two real traps found and worked around before any real fetch was trusted**: (1) the plain JSON mode silently caps every response at 70 rows regardless of the requested date range — confirmed live (a full-year request returned the identical 70 rows as a single busy day); adding `&csv=true` returns the same data fully uncapped (confirmed via smooth, monotonic yearly totals: 2016 6,236 bulk rows -> 2025 19,407, not a step change consistent with hitting some other cap). (2) The csv mode has its own ~1-year date-range cap (HTTP 500 beyond that), so the fetch is chunked by calendar year (~22 requests for 2016-2026 x 2 report types) rather than per-day. Raw `urllib.request` reconnects cost ~10s/request; switching to a persistent `requests.Session()` dropped this to 0.05-0.2s/request, the difference between infeasible and a ~2-minute fetch.

**Two real parsing/write bugs caught by smoke-testing before any signal was scored (not the code-review's own findings — my own pre-review due diligence, consistent with this project's standing practice of checking a fetcher's output before trusting it):** (1) NSE's CSV export formats `Quantity Traded` with Indian lakh/crore comma grouping (`"9,08,279"`, not thousands grouping) — a naive `pd.to_numeric()` on the raw string silently NaNs almost every row (a first smoke-test of just 2016 returned 22 parsed rows against the same query's own unparsed count of 6,236); fixed by stripping commas before conversion. (2) `write_atomic()`'s temp-then-rename save wrote to a `.tmp` extension, and pandas' `to_csv(..., compression="infer")` infers compression from the FINAL suffix only — so a `.csv.gz.tmp` temp file was silently written as plain text, then renamed to a `.csv.gz` that gzip couldn't read; fixed by passing `compression="gzip"` explicitly rather than relying on inference.

**Design**: the sparse, per-stock-irregular shape of this data (most stock-days have no bulk/block deal at all) doesn't fit the project's existing daily cross-sectional rank-IC framework (Entries 82-86, built for a statistic computed every day for every stock) — the natural test is an EVENT STUDY, directly generalizing `probe_holiday_effect.py`'s single-instrument `subset_control` random-day null to multiple stocks: for each stock, draw the same NUMBER of random dates from ITS OWN eligible-date population (where a forward return is computable) as it has real events, pool across every stock, repeat 3,000 times. Universe: N>=15 same-direction events for bulk deals (data-rich, 149K+ rows), N>=1 for block deals (data-sparse — only 5-21 stocks EVER reach even 2 same-direction block events in the whole window, confirmed by a direct count before choosing this threshold). Lag-1 fill (enter at close of event_date+1, exit at close of event_date+1+h, h in {1, 5, 10, 21}) — no look-ahead needed even at this design stage, since the fill-lag convention was applied correctly from the start rather than retrofitted. Signal direction fixed in advance per the "follow the smart money" thesis: BUY (buy_qty > sell_qty that day) => higher forward return expected, SELL => lower. Registered: 2 deal types x 2 sides x 4 horizons = 16 tests.

**Result: a clean null across all 16 cells.** p ranges 0.19 to 1.00 (worst case, `bulk_deals BUY h=21`, at p=1.0000 — the actual pooled mean sat inside the bulk of its own null distribution); the closest to significance is `bulk_deals SELL h=10` at p=0.2549, nowhere near an uncorrected 0.05. No cell's chronological halves are even both same-signed with the pooled mean in most cases, so the decision rule's `advance` column is False across the board — 0/16, not a near-miss.

**Independent code-review findings and fix status (8 total, before this write-up).** FIXED: (1, CRITICAL) `event_test()`'s "halves" reporting was alphabetical-by-symbol, not chronological — `real_returns` was built by iterating `events_by_symbol.items()` symbol-by-symbol (alphabetical order from `build_universe`'s `sorted(syms)`), so the first/second-half split was a split by stock alphabet, not by time, contrary to what "both halves" means everywhere else in this file. Fixed by accumulating `(date, return)` pairs and sorting by `pd.Timestamp(date)` before splitting. (2) `fetch()`'s per-request exception handler now also catches `KeyError` (a 200-OK response whose CSV/columns don't match `parse_csv()`'s expectations, e.g. a transient NSE error page served with a 200 status). (3) `period="10y"` -> `"max"` at all 4 call sites (closing a truncation-vs-docstring-claim mismatch: the docstring already claimed reaching back to 2016, but `load_closes`'/`nse_calendar`'s default period would have silently truncated the analyzed window to the last 10 years). (4) `eligible_dates()` now also checks `p0>0` (the entry price being positive), matching `fwd_return()`'s validity check exactly rather than approximately. (5) The initial `new_session()` call in `fetch()` is now wrapped in try/except (a network failure there previously crashed the whole fetch instead of failing gracefully with "safe to rerun"). (6) A dead `ok_dates` variable (computed, never used) was removed as part of the halves-chronology fix. ACCEPTED AS-IS: (7) `save()`'s O(n) full-rewrite-per-iteration — harmless at the current ~22-request fetch scale. (8) the ~9th copy of the seeded-random-control pattern across this project's probe scripts — documented cross-project debt, not this entry's job to fix.

**No lot-size fix was needed** (equities, not derivatives — the capital-tier wall Entries 7/9/25/26/29/45 mapped out for options/futures/commodity contracts doesn't apply here), and the lag-1 fill convention was correct from the start of this entry (unlike Entry 59's retroactive fix to the older rotation family) — this entry's own event-study design used it from the first line of code, per this project's now-standard practice.

**Net verdict.** Hundredth mechanism, and the cleanest possible negative result for a genuinely untested signal dimension: a real, freely fetchable, previously-untried data source, a design matched to its actual (sparse, irregular) shape rather than forced into an ill-fitting existing framework, and a null result across every registered cell rather than a hint requiring further chasing. 16 p-values registered in `multiple_comparisons.py` (honest family now 470 registered + 484 unregistered scan cells = m=954, Bonferroni threshold 0.00005; none of the 16 comes within two orders of magnitude of it, and none would have needed to — the null here is real, not a marginal miss). 271 tests pass (6 new, `tests/test_probe_bulk_deals_signal.py`). **89 mechanisms tested; IBS rotation remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-first: Zweig Breadth Thrust — a genuinely new data dimension (market breadth,
not one instrument's own price/vol), and the literal published rule fires zero times in 20
years on two independent NSE universes

Per the "IBS rotation's significance claim is retired, search for something genuinely
different" ordering this project has followed since the Fifty-third entry. Every prior
market-timing overlay tried here — the Sixty-first/Sixty-second entries' price-based NIFTY
SMA trend gates, the Fifty-seventh/Fifty-eighth entries' India-VIX-spike fear-buy — conditions
on a SINGLE instrument's own price level or implied volatility. Martin Zweig's Breadth Thrust
("Winning on Wall Street", 1986) conditions on something this project has never aggregated
before: how many DIFFERENT stocks are participating in a move at once, across a whole
universe. The raw ingredient (daily closes for the 52-stock `WIDE_UNIVERSE` and the 54-stock
`UNIVERSE_B`, already fetched throughout Entries 38-99) was sitting in this project's own
infrastructure unused for exactly this purpose.

**Zweig's rule, implemented exactly as published — unusually precise for a decades-old
technical rule, so no reinterpretation was needed:**
- Daily breadth ratio = advances / (advances + declines) across the universe; unchanged names
  excluded from both, the standard convention.
- "10% trend" = an EXPONENTIAL moving average of that ratio with smoothing constant 0.1
  (alpha=0.1) — explicitly NOT a 10-day window, a naming trap this entry avoided by reading
  Zweig's own terminology carefully: today's trend = yesterday's trend + 10% of the gap to
  today's raw ratio (`breadth.ewm(alpha=0.10, adjust=False).mean()`).
- A "thrust" fires when the 10% trend rises from <= 0.40 to >= 0.615 within 10 trading days or
  fewer — a rare, large, rapid swing from oversold to strongly positive breadth. Zweig's own
  claim (NYSE data, 1945-1986): every such thrust up to that point was followed by a strong
  advance over the following months, with only a handful of occurrences in 40 years —
  EXPLICITLY a rare, low-n signal by the letter of the rule, not a parameter this project
  chose to make thin.

Implemented as `probe_breadth_thrust.py`. Tested on two independent, disjoint universes
(`WIDE_UNIVERSE`, `UNIVERSE_B` — the Sixty-eighth/Sixty-ninth entries' own cross-check
convention) as breadth sources, 20y (deliberately longer than this project's usual 10y
rotation window, specifically so a rare signal gets a real chance to fire, and so it can be
checked against 2009's crash bottom — historically one of the most famous real-world
Zweig-thrust-qualifying events in US markets). Forward NIFTY return at 21/63/126/252 trading
days, lag-1 fill (decision known at the event day's close, enter the NEXT close — this
project's standard no-lookahead convention), against the random-day-pool null used throughout
the calendar/event-study entries since the Ninety-second (`probe_fear_followup.random_pool_p`).

**Result: zero qualifying events, on both universes, over the full 20-year window.** Diagnostics
(run before concluding this was a bug, not a finding): universe A's breadth trend genuinely
reaches both extremes (129 days <=0.40, 39 days >=0.615 over 20y) and its FASTEST observed
low-to-high transition, with no window cap at all, was 12 trading days (2009-03-09 to
2009-03-27) — missing Zweig's own 10-day requirement by just 2 trading days, on the single most
famous real-world thrust episode in market history. Universe B's fastest transition was 19
trading days. Neither universe produces a single event that clears the literal rule; there is
no event-study p-value to register (nothing to test — correctly reported as "no events," not
manufactured into a null p-value for its own sake).

**A relaxed 15-day window (explicitly exploratory, post hoc, n too small for any p-value —
same discipline the macro-analog entry (Fifty-seventh) applied to its own single-digit-n
oil-shock episodes) surfaces exactly the episode the diagnostics predicted, and it does not
replicate across universes.** Universe A: 2 events — 2009-03-27 (forward NIFTY: +21.7% at 21d,
+48.6% at 63d, +68.0% at 126d, +77.1% at 252d, the real 2009 rally) and 2025-03-24 (weaker and
inconsistent: +2.8%/+8.3%/+4.2%/-3.0%). Universe B: STILL zero events even at 15 days — the one
relaxed-window "hit" on universe A doesn't reproduce on an independent 54-stock sample of the
same market over the same window, meaning it reads as "2009 was an extreme enough crash-then-
rally that this particular 52-stock sample's breadth happened to qualify within 15 days," not a
robust cross-sectional breadth phenomenon.

**Secondary, cheap reuse of the same breadth data**: a continuous "% of universe above its own
200-day SMA" gate on IBS rotation (universe A, 10y — matching the window the Sixty-first entry's
own price-based NIFTY-SMA gate used, for direct comparability), reusing `simulate()`'s existing
`gate=` parameter and the identical random-off-months control unchanged. Result: the breadth
gate actively UNDERPERFORMS both the ungated baseline and its own random-off-months control at
both portfolio sizes — top_k=5: ungated 20.12%/yr (39.5% maxDD) vs breadth-gated 14.73%/yr
(22.8% maxDD) vs random-off-months 15.95%/yr (34.3% DD), p(return)=0.575, p(drawdown as low
as)=0.104; top_k=8: ungated 19.07%/yr vs breadth-gated 12.24%/yr vs random 14.98%/yr,
p(return)=0.778, p(drawdown)=0.201. Compare to the Sixty-first/Seventy-sixth entries' own
price-only NIFTY-SMA gate, which DID beat its random-off-months control on drawdown at several
SMA lengths (p(DD) as low as 0.013-0.077) — the breadth-based gate is not merely "no better,"
it is measurably worse at the one job (cutting drawdown without giving back more return than a
coin-flip) the existing price-based gate was shown to do.

**Net verdict.** A genuinely new data dimension, correctly implemented against a rule precise
enough that no interpretation judgment calls were needed, tested with the full rigor this
project applies elsewhere (two independent universes, no-lookahead fills, a random-day-pool
null, an honest report of a small-n exploratory check clearly separated from the pre-registered
literal-rule test) — and it produces the cleanest possible negative for an event-based signal:
the event simply never happens, on this market, at this rule's literal thresholds, even at the
one moment (2009) it should have been most likely to. The likely reason, worth recording: Zweig
calibrated 0.40/0.615/10-days against NYSE-wide breadth (thousands of names), whose day-to-day
advance/decline ratio is far smoother than a 52-54-stock sample's; the same alpha=0.1 smoothing
constant applied to a noisier, narrower universe needs a wider recovery window to swing the same
distance, which is exactly what the diagnostics show (12-19 trading days for the fastest real
transition vs the rule's 10-day requirement) — a genuine calibration mismatch between a rule
built for a much broader universe and this project's own necessarily-smaller universes, not
evidence the underlying "does breadth predict?" question has no answer here. 4 p-values
registered in `multiple_comparisons.py` (the breadth-gate's return/drawdown tests at both
portfolio sizes; honest family now 474 registered + 484 unregistered scan cells = m=958,
Bonferroni threshold 0.00005 — none of the 4 comes remotely close, consistent with how clearly
they failed). 276 tests pass (5 new, `tests/test_probe_breadth_thrust.py`, covering the EMA
formula, the exact 10-vs-11-day boundary, declustering, and NaN-warmup safety). **90 mechanisms
tested; IBS rotation remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-second: Nifty 50 index-reconstitution drift — a forced-FLOW mechanism, genuinely different from every prior signal in this project, and another clean null

*(Renumbered from this entry's original "Hundred-and-first" / "101st" label — written concurrently
with the Zweig Breadth Thrust entry above on separate branches, both independently claiming Entry
101. Zweig merged first; this entry became 102. Only the entry number and the mechanism/ledger
running counts below were changed from the original text — the analysis, data, and verdict are
unchanged.)*

Sourced from a background forums/niche-source research pass (not this project's own prior probe lineage) looking specifically for a mechanism not already covered by the 89 tested so far. Every signal in Entries 1-100 comes from price, volume, options OI, delivery percentage, dividends, a disclosed trade (bulk/block deals, Hundredth entry), or the calendar. NSE Indices reviews the Nifty 50 semi-annually; an addition forces every fund tracking the index (ETFs, index funds) to buy the new constituent on the effective date, a deletion forces the same funds to sell - the mechanism is passive-money rebalancing, not information, momentum, or mean-reversion. `probe_nifty_reconstitution.py`, pre-registered in its own docstring before any signal was scored; independent `/code-review` before this write-up (3 findings, all fixed - see below).

**Data compilation, and two honesty adjustments made before any signal was scored.** No NSE API serves historical index-membership changes (absent from every endpoint this project's other probes already use). A background research pass compiled 69 dated events from Wikipedia's "NIFTY 50" article, which individually footnotes each effective date to an NSE press release or major financial-news report. (1) **Anchored on the effective date, not the announcement date** - the compiled announcement dates are unreliable (several are a retrospective news article published years later, or a date that postdates the effective date outright), while the effective date is both individually sourced for every row and the economically correct anchor anyway (passive funds must transact AT this date; that is the forcing mechanism). (2) **8 of the 69 compiled events excluded** for having no citation at all (flagged by the research pass as "no independent citation, just a Wikipedia table row with no ref tag"): 2005-09-26, 2006-06-27 (x2), 2006-09-01, 2008-03-14 (x2), 2010-04-08, 2011-03-25. 61 events remain, in `EVENTS`.

**Design**: reuses `probe_bulk_deals_signal.event_test`/`load_closes`/`HORIZONS`/`DRAWS`/`COST_RT` verbatim rather than re-deriving the same already-tested logic (RESULTS.md's own open item flags this project's duplicated-helper problem; not adding to it here). Matched-stock random-day null (per-stock, draw the same count of random dates from that stock's own eligible-date population, pool across stocks, 3,000 draws) - the same design the Hundredth entry validated at n=1 real event per stock, which is the common case here too (index reconstitution is rare for a given name). Lag-1 fill (enter at close of effective_date+1, exit at close of effective_date+1+h, h in {1, 5, 10, 21}). Signal direction fixed in advance: ADD => expect positive (forced buying), DELETE => expect negative (forced selling). 85 distinct symbols across both sides; 69 resolved on Yahoo, 16 excluded (delisted, renamed, or a wrong guessed ticker among the research pass's flagged-uncertain spellings - fetch-and-skip, the Hundredth entry's established convention, not a silent mis-score). Registered: 2 sides x 4 horizons = 8 tests.

**Result: a clean null across all 8 cells.** ADD: 49 events/48 stocks, p ranges 0.10-0.44 (closest to significance, ADD h=10: +2.29% gross, both chronological halves positive at +1.81%/+2.76%, but p=0.0953 does not clear even an uncorrected 0.05). DELETE: 43 events/40 stocks, p ranges 0.73-0.92, nowhere near significant in either half. 0/8 cells pass the pre-registered decision rule.

**Independent code-review findings and fix status (3 total, before this write-up).** FIXED: (1) the pre-registration docstring claimed "8 of 70 compiled events" excluded, but the actual `EVENTS` list has 61 rows (61+8=69, not 70) - a wrong round number in the draft text, not a data error; corrected to the exact reconciling count. (2) `HORIZONS`/`DRAWS`/`COST_RT` were redefined locally with values copied from `probe_bulk_deals_signal.py` instead of imported, risking silent drift if that module's cost assumption is ever revised (this project's own history shows transaction-cost estimates have been revisited before, Entry 73); now imported directly. (3) a stale test comment referencing a "GRASIM: DELETE 2010-10-01" event that doesn't exist in the data (leftover from an earlier draft) was corrected to describe what the adjacent assert actually checks.

**Net verdict.** Ninety-first mechanism, and - like the Hundredth entry before it - a genuinely new signal FAMILY (forced flow, not price/volume/OI/calendar/disclosed-trade) tested cleanly and rejected rather than left unexamined. 8 p-values registered in `multiple_comparisons.py` (honest family now 482 registered + 484 unregistered scan cells = m=966, Bonferroni threshold 0.00005; the closest cell, ADD h=10 at p=0.095, is nowhere near it). 279 tests pass (3 new, `tests/test_probe_nifty_reconstitution.py`, on top of the Zweig entry's 276). **91 mechanisms tested; IBS rotation remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-third: does an ATR-based stop/target during the hold improve IBS rotation? It cuts drawdown a lot, costs more return than it saves, and only one grid cell comes out ahead on Calmar

Requested check: whether adding the kind of per-trade ATR stop-loss/take-profit this project uses
throughout its single-instrument daily strategies (RSI-2, Squeeze, MACD, SuperTrend, DMI/ADX, SAR
— all size an ATR-scaled stop at entry) would improve IBS rotation, this project's sole standing
finding, which currently has NO intra-month exit condition at all: a pick is held from the lag-1
entry fill straight through to the lag-1 exit fill at the next month-end, no matter what happens
to the stock in between.

Added `compute_atr()` (vectorized per-stock ATR, the same plain-rolling-mean convention
`indicators.average_true_range` and SuperTrend's own ATR already use, not Wilder's exponential
smoothing) and an opt-in `atr`/`stop_mult`/`target_mult` triple to `probe_reversal_rotation.py`'s
existing `simulate()` (default `None`, byte-for-byte unchanged behavior when omitted — verified by
rerunning the file's own `--validate` baseline). When set, each pick's stop/target is sized off
ITS OWN ATR as of the ranking date (no lookahead, same convention as every other ATR-sized stop
in this project), then checked day-by-day from the entry fill through the scheduled exit: a low
piercing the stop or a high clearing the target exits early at that trigger price (stop wins a
same-day tie, this project's established adverse-first convention); a pick that never triggers
exits at the scheduled month-end close exactly as before. Pre-registered grid (mirroring the
R-multiple ranges this project's other ATR-stopped strategies already use — RSI-2's
`stop_atr_multiple=3.0`, 3-bar breakout's `target_r_multiple=2.5`): `stop_mult` in {1.0, 1.5, 2.0}
x `target_mult` in {2.0, 3.0, 4.0}, IBS(5), top_k=5, lag=1, the family's own 52-stock
`WIDE_UNIVERSE`, 1,500-seed same-stop/target random-portfolio control. `--atr-stop` in
`probe_reversal_rotation.py`.

**Baseline (no stop/target, this run's fresh data pull): 19.60%/yr, max drawdown 39.5%** — in line
with the Fifty-ninth entry's own ~20%/yr lag-1 figure, small drift only from the usual
yfinance-snapshot variance already documented throughout this file.

**Every one of the 9 cells returns LESS than the baseline — a tight stop clips the bounce, not
just the downside.** Full grid:

| stop | target=2.0 | target=3.0 | target=4.0 |
|---|---|---|---|
| 1.0xATR | 6.05%/yr, DD 21.9%, Calmar 0.28 | 7.92%/yr, DD 15.1%, Calmar 0.52 | **10.16%/yr, DD 13.1%, Calmar 0.78** |
| 1.5xATR | 5.42%/yr, DD 23.2%, Calmar 0.23 | 7.51%/yr, DD 19.6%, Calmar 0.38 | 10.91%/yr, DD 20.3%, Calmar 0.54 |
| 2.0xATR | 5.31%/yr, DD 20.3%, Calmar 0.26 | 7.60%/yr, DD 25.1%, Calmar 0.30 | 11.14%/yr, DD 25.8%, Calmar 0.43 |

(Baseline Calmar: 19.60/39.5 = 0.50.) Drawdown drops hard everywhere (13-26% vs 39.5%), but return
drops harder in every cell except one. **Only `stop=1.0xATR, target=4.0xATR` beats the baseline's
own Calmar** (0.78 vs 0.50) — a real, if modest, risk-adjusted improvement: a TIGHT stop paired
with a WIDE target (4:1 reward:risk) gives up about half the raw return (10.16% vs 19.60%) for
two-thirds less drawdown (13.1% vs 39.5%). That cell's quarters are also clean — all 4 positive
(+8%/+64%/+40%/+5%), no decay — and both walk-forward halves are strongly positive (+65%/+58%).
Every cell still beats its own same-stop random-portfolio control (p=0.006-0.11, not corrected for
multiple comparisons, consistent with this being an exploratory check rather than a fully
registered family), meaning the underlying stock-selection edge over random survives the stop/
target overlay — it's being diluted by the stop mechanics, not erased by them.

**Why a stop mostly hurts here, consistent with this project's own prior finding on the same
question for a different strategy**: the Eleventh entry already found that bolting an ATR trailing
STOP (not even a hard stop-and-reverse) onto RSI-2's own exit rule "actively hurts, doesn't help,"
because RSI-2's own exit already functions as a profit target tuned to its mechanism. IBS rotation's
picks are specifically stocks that just closed near their own low — exactly the volatility profile
most likely to breach a 1-2xATR stop in the days immediately after entry, before the Fifty-ninth/
Sixty-sixth entries' own documented bounce (which accrues mostly in the first 5-10 trading days)
has time to complete. A tight stop doesn't protect against a thesis that's wrong; on this specific
entry signal it mostly cuts off the bounce mid-flight.

**Net verdict.** Not a new mechanism (an exit-overlay test on the standing finding, same category
as the Eleventh entry's RSI-2 profit-booking overlay and the Forty-sixth/Forty-seventh entries' ETF
beta hedge) — no count change. A per-trade ATR stop/target is a real, usable lever for cutting IBS
rotation's drawdown, but it costs more return than it saves at every setting except one (tight stop,
wide target), and even that cell's Calmar gain (0.50 -> 0.78) is smaller than the half-hedge
overlay's own (0.51 -> ~1.0-1.1, Sixty-first entry) — the NIFTY trend gate and the NIFTYBEES half-
hedge both remain better-corroborated risk-reduction options on top of this project's sole standing
finding than an ATR stop/target is. Not registered in `multiple_comparisons.py` (exploratory, not a
formally pre-registered significance claim) and not run through quarter-split/perturbation/
survivorship-stress beyond what's shown above — a deeper pass would only be worth it if this project
decides to actually pursue the one-good-cell (1.0xATR/4.0xATR) further. 279 tests pass (unchanged —
additive, default-off parameters, verified against the file's own `--validate` baseline). **IBS
rotation remains the sole standing finding; nothing is declared tradable.**

**Follow-up on the same cell: quarter-split detail and a perturbation sweep (`--atr-stop-detail`,
`--atr-stop-perturb`).** Quarter-split (annualized per ~2.5y chunk, with its own random-portfolio
control, same stop/target applied to both): **Q1 +4.77%/yr (random -0.0%, p=0.16); Q2 +16.94%/yr
(random +62.3%, p=0.72 — a strong bull quarter where random picks did even better, so the edge
isn't distinguishable from beta here); Q3 +14.26%/yr (random +14.6%, p=0.03); Q4 +5.07%/yr (random
-8.4%, p=0.03).** All 4 quarters positive — no decay, and Q4 (the most recent, most relevant
window) clears its random control the most cleanly of any quarter. Q2 is the one weak link: not a
loss, just not distinguishable from random stock-picking in a quarter strong enough that almost
any basket worked.

**Perturbation (stop_mult in {0.75, 1.0, 1.25, 1.5} x target_mult in {3.0, 3.5, 4.0, 4.5, 5.0}, 20
cells): smooth and monotonic everywhere, zero walk-forward sign flips — real robustness, not a
single-point-fit.** Return and Calmar both rise as the target widens at every stop setting tested
(e.g. stop=1.0: 7.92%/13.1%DD/Calmar 0.53 at target=3.0 climbing to 12.45%/14%DD/Calmar 0.91 at
target=5.0) — no cliff anywhere in the grid. **This also means (1.0xATR, 4.0xATR) is not a local
peak — it sits partway up a ridge the pre-registered grid's own edge (target=5.0) keeps climbing**;
the best Calmar in this sweep is actually stop=1.0xATR/target=5.0xATR (0.91), not the originally
flagged cell. Consistent with the earlier entry's own read: a tight stop with a wide enough target
converges toward "barely constrain the upside, just cap the downside," approaching the no-stop
baseline's return as the target widens further — the grid wasn't swept wide enough to find where
(if anywhere) that ridge turns over, since this was a bounded, pre-registered check rather than an
open-ended optimization.

**Net addition.** The flagged cell is robust (smooth neighborhood, no decay, no cliff) but was an
arbitrary point on a monotonic surface, not a validated optimum — per this project's own standing
practice (the Fifteenth/Seventeenth/Thirty-fourth entries' repeated warning against trusting a
result that "peaks suspiciously close to the exact default"), this is the opposite problem: nothing
peaks at all within the tested range, which argues against over-interpreting this exact combination
as special. If this line is pursued further, the honest next step is widening the target_mult axis
past 5.0 to find where the curve actually turns over (or confirm it doesn't within any sane range,
which would mean the "stop/target" framing is doing less work than a plain wide stop alone would).
Not done here — out of scope for a single follow-up check. **IBS rotation remains the sole standing
finding; nothing is declared tradable.**

**Follow-up: widening target_mult to find where it actually turns over (`--atr-target-widen`).**
Swept target_mult from 4.0 out to 1000 (effectively "stop-only, target never triggers") at
stop=0.75/1.00/1.25xATR. **It does turn over — it doesn't climb forever.** Return and Calmar both
rise sharply from target=4 to ~6-8, then FLATTEN, converging to the stop-only asymptote by
target~20-30 (target=30 and target=1000 give identical numbers at every stop level, confirming the
target has stopped mattering well before 1000). All cells remain walk-forward consistent — no sign
flips anywhere in the widened range either.

| stop | best cell in this sweep | return | maxDD | Calmar |
|---|---|---|---|---|
| 0.75xATR | target=8 | 12.45%/yr | 12% | **1.07** |
| 1.00xATR | target=6 | 12.87%/yr | 13% | 0.96 |
| 1.25xATR | target=6 | 15.19%/yr | 17% | 0.88 |

The actual Calmar peak across this wider sweep is **stop=0.75xATR, target~8xATR (Calmar 1.07)** —
better than both the originally flagged (1.0, 4.0) cell (0.77) and the first perturbation sweep's
edge-of-grid "best" (1.0, 5.0, Calmar 0.91). Past target~10-15, widening the target further buys
nothing: the position almost never actually hits a target that wide before either the stop fires
or the month-end exit arrives, so performance flatlines at essentially "stop-only" — e.g. at
stop=1.0xATR the stop-only variant nets 13.59%/yr at 15% drawdown (Calmar 0.91), only modestly below
the best target=6 cell (12.87%/yr... note non-monotonic: actually target=8 edges target=6 slightly
higher, 13.76%/yr Calmar 0.93 — the true peak at stop=1.0 is ~target=6-8, essentially tied).

**Net reading.** The target_mult axis has a real, moderate interior optimum around 6-8xATR (not an
unbounded ridge, correcting the previous follow-up's open question) — the earlier (1.0, 4.0) and
(1.0, 5.0) cells were both short of it, not past it. The practical takeaway is blunter than the
exact peak location, though: **almost all of the Calmar improvement over the no-stop baseline comes
from the STOP, not the target** — once the target is wide enough to rarely fire (>=15xATR), results
are statistically indistinguishable from a tight ATR stop with no profit target at all, and that
"stop-only" variant already captures most of the gain (Calmar 0.83-1.07 across the three stop
levels tested, vs the original-grid's best of 0.77-0.91). A simpler, one-parameter "tight stop, let
it run to month-end or stop out, no separate target" rule would likely do about as well as the
two-parameter version this check set out to tune — worth remembering before adding a second knob to
a risk overlay without first checking whether the first one is doing all the work. **IBS rotation
remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-fourth: full-rigor pass on the ATR-stop-only variant — robust and significant on the
universe it was found on, decisively fails cross-universe replication

Direct follow-up requested on the Hundred-and-third entry's own finding that the target leg does
almost no work — this entry puts the simplified ATR-STOP-ONLY rule (target effectively disabled,
`STOP_ONLY_TARGET=1000`) through this project's full standing checklist rather than treating the
earlier screens as sufficient: (1) walk-forward + 1,500-seed significance per cell, (2) quarter-
split detail, (3) survivorship stress (the Fortieth entry's 4 real blowups, 56 stocks), (4) cross-
universe replication (`UNIVERSE_B`, 54 different NSE names, zero overlap with `WIDE_UNIVERSE`) — a
candidate only counts as a real survivor if it clears ALL four, the same standard this project has
already applied to SuperTrend's `CL=F`, IBS's own FX retest, and 52-week-high's widening (all of
which looked clean on the first screen and failed on a later one). `--stop-only-rigor` in
`probe_reversal_rotation.py`. Pre-registered grid: `stop_mult` in {0.5, 0.75, 1.0, 1.25, 1.5, 2.0},
IBS(5), top_k=5, lag=1.

**(1) Screening, base 52-stock universe: all 6 cells pass cleanly.** Baseline (no stop): 19.60%/yr,
39.5% drawdown, Calmar 0.50. Every stop level is walk-forward CONSISTENT and beats its own
same-stop random-portfolio control at uncorrected p<0.05:

| stop | return/yr | maxDD | Calmar | p |
|---|---|---|---|---|
| 0.50xATR | 10.46% | 10.6% | 0.98 | 0.0053 |
| **0.75xATR** | **12.28%** | **11.6%** | **1.06** | 0.0220 |
| 1.00xATR | 13.59% | 14.9% | 0.91 | 0.0213 |
| 1.25xATR | 15.53% | 18.7% | 0.83 | 0.0180 |
| 1.50xATR | 14.98% | 22.0% | 0.68 | 0.0346 |
| 2.00xATR | 15.20% | 27.3% | 0.56 | 0.0440 |

Smooth, monotonic Calmar decline as the stop widens — no cliffs, confirming the Hundred-and-third
entry's own perturbation finding holds on this wider grid too. Best two by Calmar: 0.75xATR (1.06)
and 0.50xATR (0.98).

**(2) Quarter-split detail on the top 2: thin but not decaying.** Both cells are positive in all 4
quarters (no decay), but only Q3 individually clears an uncorrected 0.05 against its own random
control for both cells (p=0.0100, 0.0133); Q4 is significant for stop=0.50 (p=0.0199, random mean
-10.3% vs the strategy's own +2.5%/yr) but not for stop=0.75 (p=0.1163, still positive at +0.65%/yr
against random's -9.5%). Q1/Q2 don't clear 0.05 for either cell — Q2 especially, where even random
stock-picking returned ~50-66%/yr in that bull stretch. Consistent with the overall screening p's:
real on average, not uniformly significant quarter-by-quarter.

**(3) Survivorship stress: holds up, even improves.** On the 56-stock blowup-stress universe
(baseline no-stop: 21.86%/yr, 36.2% DD): stop=0.75xATR reaches 14.39%/yr at 13.0% DD, **Calmar 1.11**
(better than the clean universe's 1.06), still CONSISTENT, p=0.0187. stop=0.50xATR: 13.57%/yr,
15.7% DD, Calmar 0.87, CONSISTENT, p=0.0053. Same "harvests the bounce rather than holding through
the collapse" signature already documented for plain IBS rotation (Fortieth entry) — the stop
overlay doesn't break that property.

**(4) Cross-universe replication: fails decisively.** On `UNIVERSE_B` (baseline no-stop: 16.02%/yr,
37.0% DD, Calmar 0.43 — already a weaker universe than `WIDE_UNIVERSE` even unstopped): **stop=0.75xATR
collapses to 5.47%/yr at 27.2% DD, Calmar 0.20, walk-forward INCONSISTENT** (first half +70%,
second half ~0%), **Q4 is NEGATIVE (-27%)**, p=0.3591 (not significant — worse than most random
draws). stop=0.50xATR: 4.66%/yr, Calmar 0.20, also INCONSISTENT, Q4 -24%, p=0.3531. The direction
of the effect reverses on this universe: on `WIDE_UNIVERSE` the stop overlay roughly doubled
Calmar (0.50 -> 1.06); on `UNIVERSE_B` it HALVES it (0.43 -> 0.20) relative to that universe's own
unstopped baseline.

**Net verdict.** Exactly the pattern this project has learned to require a retest for and has seen
fail before (SuperTrend's `CL=F`, the Eighteenth entry's retest; IBS's own FX extension, the
Twenty-fifth entry) — a result that is smooth, significant, and survivorship-robust on the one
universe it was found on, and falls apart (inconsistent, Q4-negative, not significant) the moment
it's asked to generalize to a different, equally real, equally Kite-tradable stock set. The
ATR-stop-only overlay is **not confirmed** — it stays a universe-specific curiosity, not a
validated risk-reduction lever, which also means the NIFTY trend gate and the NIFTYBEES half-hedge
(both independently corroborated across more than one check in earlier entries) remain the better-
evidenced options if a drawdown overlay is wanted on top of IBS rotation. 10 p-values registered in
`multiple_comparisons.py` (quarter-level p's, 8 cells, added to `UNREGISTERED_SCAN_CELLS` instead,
same convention as the Sixty-fourth entry's phase/anchor scan); honest family now 492 registered +
492 unregistered = m=984, Bonferroni threshold 0.00005 — none of the 10 comes close, and the
honest-family winners list is unchanged (still just the two S&P 500 index-gate drawdown rows). 279
tests pass (no new logic needing a test — additive, default-off parameters built entirely from
already-tested `simulate()`/`compute_atr()`). **IBS rotation remains the sole standing finding;
nothing is declared tradable.**


## Hundred-and-fifth: full-rigor pass on the NIFTY SMA trend gate — the drawdown effect holds
directionally but loses almost all its significance on a second universe, and the honest
Bonferroni family has now grown past its own best surviving finding's resolution floor

Direct follow-up to the Hundred-and-fourth entry: the NIFTY trend gate (Sixty-first/Seventy-sixth
entries) was named there as the "better-evidenced" alternative to the ATR-stop overlay, but it had
never actually been put through the one check that just sank the ATR-stop variant — cross-universe
replication on `UNIVERSE_B` (54 different NSE names). This entry reruns the full battery (screening,
quarter-split detail, survivorship stress, cross-universe replication) on the gate fresh, in one
consistent pass, rather than trusting the earlier entries' numbers (run on slightly different code/
data snapshots) at face value. `--gate-rigor` in `probe_reversal_rotation.py`; pre-registered grid:
SMA in {100, 150, 200}, IBS(5), top_k=5, lag=1.

**(1) Screening, base 52-stock universe — Calmar roughly doubles, but the random-control p-values
are weaker than the earlier entries' own headline numbers.** Baseline (ungated): 19.60%/yr, 39.5%
drawdown, Calmar 0.50.

| SMA | return/yr | maxDD | Calmar | p(return) | p(drawdown) |
|---|---|---|---|---|---|
| 100 | 15.34% | 14.3% | 1.08 | 0.268 | 0.0426 |
| 150 | 15.44% | 14.8% | 1.04 | 0.317 | 0.0420 |
| 200 | 15.47% | 17.8% | 0.87 | 0.371 | 0.0806 |

Return never clears even an uncorrected 0.05 (confirming the Seventy-sixth entry's own "the gate
does not time returns better than random months" on NIFTY specifically) — the return numbers above
are indistinguishable from randomly picking the same number of off-months. Drawdown is the real
claim, and on THIS fresh run it's thinner than previously reported: SMA100/150 sit right at
p≈0.042-0.043 (barely under 0.05), SMA200 misses (p=0.081). Both halves are consistent and all
quarters positive for every SMA length — no decay, matching earlier entries.

**(2) Quarter-split detail on the top 2 (SMA100, SMA150): no individual quarter is significant.**
Every one of the 8 quarter-cells (4 quarters x 2 SMA lengths) has p in the 0.19-0.82 range against
its own random-off-months control — weaker than IBS rotation's or the ATR-stop line's own
quarter-level results, which had at least one or two quarters individually clearing an uncorrected
0.05. The drawdown benefit, such as it is, isn't concentrated in — or absent from — any particular
quarter; it's a diffuse, whole-period effect too thin to localize.

**(3) Survivorship stress (4 real blowups, 56 stocks): holds up, SMA150 improves.** SMA100: 18.71%/yr,
20.8% DD, Calmar 0.90 (down from the clean universe's 1.08), p(return)=0.179, p(DD)=0.134 — weaker
than the base screen. SMA150: 19.52%/yr, 14.8% DD, **Calmar 1.32** (up from 1.04), p(return)=0.177,
p(DD)=**0.0173** — the single best drawdown p-value in this whole entry, and consistent with the
Sixty-first entry's own earlier survivorship-stress finding that SMA150 is the more robust length
under stress.

**(4) Cross-universe replication (UNIVERSE_B): the Calmar advantage reverses.** `UNIVERSE_B`'s own
ungated baseline: 16.02%/yr, 37.0% DD, Calmar 0.43. Gated: SMA100 10.45%/yr, 30.8% DD, **Calmar
0.34** (worse than ungated); SMA150 12.11%/yr, 31.5% DD, **Calmar 0.38** (also worse than ungated).
Both walk-forward halves stay positive (CONSISTENT, unlike the ATR-stop line's INCONSISTENT flip —
this failure is milder in kind) but neither the return nor the drawdown effect is distinguishable
from random on this universe (p(return) 0.41-0.51, p(DD) 0.37-0.38) — the SAME direction of failure
the ATR-stop-only variant showed, just less dramatic: on the universe it was found on, the gate
improves risk-adjusted return; on an equally real, disjoint 54-stock universe, applying it makes
things slightly worse, and that difference is not statistically real either way.

**A side effect of registering these 14 p-values, worth recording honestly rather than glossing
over: the honest family has now grown large enough that this project's own best-ever surviving
finding no longer clears its corrected bar.** The S&P 500 index-gate drawdown rows (Sixty-second
entry, 20,000-draw control) were registered at the exact resolution floor, `0.00005` — previously
just under the Bonferroni threshold (0.05/984 ≈ 0.0000508) and so the only rows ever to pass. Adding
this entry's 14 rows pushes the honest family to m=1006, Bonferroni threshold ≈0.0000497 — strictly
BELOW the floor value those rows are stuck at. **`multiple_comparisons.py`'s honest-family winners
list is now empty.** This isn't new evidence against that finding (nothing about the S&P result
changed), it's a mechanical consequence of a resolution-floor value losing a race against a growing
denominator — but it's the first time in this project's history that literally nothing survives the
honest Bonferroni bar, and it's worth remembering before citing "the S&P index-gate rows are the one
thing that passes" again without rechecking the current m.

**Net verdict.** Same shape of result as the Hundred-and-fourth entry's ATR-stop overlay, one notch
milder: real and consistent on the universe it was discovered on (Calmar roughly doubles, quarters
don't decay), but the statistical case was already thin (return never significant, drawdown only
borderline) and the one check that matters most — an independent stock universe — shows the
drawdown advantage reversing rather than replicating. Per this project's own standard, **not
confirmed** as a cross-market-robust overlay for IBS rotation, though it fails more gently than the
ATR-stop line did (directionally consistent rather than flipping sign). Of the two drawdown overlays
tested this way so far, neither earns unqualified trust; the NIFTYBEES half-hedge (Forty-sixth/
Forty-seventh entries, itself not yet retested on UNIVERSE_B either) is the one remaining
candidate worth the same treatment if this line continues. 14 p-values registered in
`multiple_comparisons.py` (quarter-level cells, 8, added to `UNREGISTERED_SCAN_CELLS` instead, same
convention as the Hundred-and-fourth entry); honest family now 506 registered + 500 unregistered =
m=1006, Bonferroni threshold 0.00005 — **no row currently passes**. 279 tests pass (no new logic
needing a test — additive, reuses `simulate()`'s existing `gate=` parameter). **IBS rotation
remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-sixth: full-rigor pass on the NIFTYBEES half-hedge — never a Calmar win on either
universe (consistent with what the Forty-sixth/Sixty-first entries already said), and the
drawdown benefit itself doesn't clear significance on an independent universe

Direct follow-up to the Hundred-and-fourth/fifth entries: the NIFTYBEES half-hedge (Forty-sixth/
Forty-seventh entries) was the third drawdown overlay this project has tried on IBS rotation and
the only one not yet put through the same four-check battery. Ported the hedge mechanism into
`probe_reversal_rotation.py`'s already lag-corrected `simulate()` (a short NIFTYBEES-equivalent
leg sized at `hedge_ratio x beta x capital`, rounded down to whole shares, same cost model as every
stock leg) rather than reusing `probe_ibs_rotation_etf_hedge.py`'s older same-bar-fill pipeline —
beta is now recomputed via `compute_beta_lag1()` from this file's own lag-1 monthly returns, not
the un-lagged ones the original entries used, so the beta value differs slightly (1.063 here vs.
1.143 originally) but the mechanism is identical. `--hedge-rigor` in `probe_reversal_rotation.py`;
pre-registered grid (the Forty-seventh entry's own sweep): hedge_ratio in {0.25, 0.375, 0.5, 0.625,
0.75}, IBS(5), top_k=5, lag=1.

**Correcting a framing slip from the last two entries' chat responses first**: this project's own
Sixty-first entry already stated plainly that the half-hedge's Calmar (0.50) was WORSE than the
unhedged baseline's (0.58) — it was only ever sold as cutting absolute drawdown "by a third" at the
cost of "keeping ~54% of the return," never as a risk-adjusted improvement. Calling it the
"better-evidenced" overlay in the two prior turns overstated it; this entry's own numbers confirm
the original, more modest framing rather than contradicting it.

**(1) Screening, base 52-stock universe — Calmar is worse than unhedged at EVERY ratio tested, by
construction, not by surprise.** Baseline (unhedged): 19.60%/yr, 39.5% drawdown, Calmar 0.50.

| ratio | return/yr | maxDD | Calmar | p (vs random stock-picks, same hedge) |
|---|---|---|---|---|
| 0.25 | 14.92% | 32.8% | 0.45 | 0.0306 |
| 0.375 | 12.54% | 29.5% | 0.43 | 0.0313 |
| 0.50 | 10.14% | 27.4% | 0.37 | 0.0313 |
| 0.625 | 7.73% | 29.2% | 0.26 | 0.0306 |
| 0.75 | 5.30% | 31.1% | 0.17 | 0.0306 |

Every ratio is walk-forward CONSISTENT, and the IBS stock-selection edge over random picking
survives being run through the identical hedge at every ratio (p≈0.03 throughout — a different
question than "does the hedge improve Calmar," which it doesn't: return falls faster than drawdown
does, monotonically, as the hedge gets heavier). Best two by Calmar: 0.25 (0.45), 0.375 (0.43) —
still both below the 0.50 baseline.

**(2) Quarter-split on the top 2: same uneven shape as the other two overlays.** Q1/Q3 borderline
(p≈0.06-0.15), Q2 not significant at all (random control earns far more in that bull stretch,
+55% vs the hedge's own +11-20%/yr), Q4 clears p<0.02 for both ratios (random control actually
negative there, -2.6%/-5.5%, vs the hedge's own +12-14%/yr). No decay in raw terms — all 4 quarters
positive for both ratios — just thin, concentrated significance.

**(3) Survivorship stress (4 real blowups, 56 stocks): holds up, Calmar improves.** Beta recomputed
fresh on the stress universe (1.123). ratio=0.25: 16.78%/yr, 30.1% DD, **Calmar 0.56** (up from the
clean universe's 0.45); ratio=0.375: 14.21%/yr, 31.5% DD, Calmar 0.45 (up from 0.43). Both
CONSISTENT, p=0.0127 (better than the base screen's 0.03) — the same bounce-harvesting-survives-
stress signature already documented for IBS rotation and both other overlays.

**(4) Cross-universe replication (UNIVERSE_B): degrades further, same direction, not a reversal.**
Beta recomputed fresh (1.046). `UNIVERSE_B`'s own unhedged baseline: 16.02%/yr, 37.0% DD, Calmar
0.43. Hedged: ratio=0.25 11.51%/yr, 35.2% DD, Calmar **0.33** (worse than Universe B's own unhedged
baseline, same direction as the base universe's own result); ratio=0.375 9.22%/yr, 34.8% DD, Calmar
0.27. Neither clears significance against random stock-picks under the same hedge (p=0.12-0.12,
vs the base screen's p≈0.03) — weaker evidence the stock-selection edge survives this specific
combination on this universe, though not inconsistent in sign (both halves stay positive, no flip).

**Net verdict, and how this compares to the other two overlays.** This is actually the MOST
internally consistent of the three drawdown overlays tested this way, precisely because it was
never oversold: it reliably does the one thing it was ever claimed to do (cut absolute drawdown at
a real cost to return and Calmar) on BOTH universes, in the SAME direction, rather than reversing
sign the way the ATR-stop overlay did or flipping a borderline significance result the way the
trend gate did. What doesn't hold up is the weaker claim implicit in treating it as "the better
option" — the drawdown-vs-random-off-months style significance test was never run on the hedge
itself in the original entries (there's no natural random-hedge-ratio null the way there was a
random-off-months null for the gate), and the one significance test that IS meaningful here (does
stock-picking still beat random under the hedge) is markedly weaker on `UNIVERSE_B` than on the
discovery universe. **Still not confirmed as a validated edge-preserving overlay** — it's a real,
consistent, but Calmar-negative drawdown-reduction tool, same as it was always described, now
independently reconfirmed under lag-corrected machinery and a second universe. Of the three
overlays this project has now tested with this battery (ATR-stop, NIFTY gate, NIFTYBEES half-
hedge), none earns unqualified trust as a Calmar improvement; this one at least doesn't contradict
itself across universes, which is a real (if modest) point in its favor over the other two. 9
p-values registered in `multiple_comparisons.py` (quarter-level cells, 8, added to
`UNREGISTERED_SCAN_CELLS` instead, same convention as the two prior entries); honest family now
515 registered + 508 unregistered = m=1023, Bonferroni threshold 0.00005 — no row passes (unchanged
from the Hundred-and-fifth entry's own finding that nothing currently clears the honest bar). 279
tests pass (additive: `simulate()` gained an `etf=`/`hedge_ratio=`/`beta=` triple, default `None`,
old behavior unchanged). **IBS rotation remains the sole standing finding; nothing is declared
tradable.**


## Hundred-and-seventh: loss attribution for IBS rotation — the stock-picking edge itself (not
just drawdown) vanishes in the highest realized-vol tercile, replicated on both universes; the
IVIX/breadth splits that looked interesting on one universe reverse on the other

Requested direction: apply a regime-conditioning framework to the data already collected, and look
specifically at what conditions precede a LOSS, not just what overlay improves Calmar (Entries 61/
76/103-106 already tested three overlays — NIFTY SMA gate, ATR stop, NIFTYBEES hedge — and none
replicated cross-universe). This entry asks the narrower question those overlays acted on without
ever checking directly: does the strategy's own realized 119-month track record actually differ by
regime, or is "regime X is bad" an assumption the overlay tests smuggled in by picking which
variable to gate on?

`probe_loss_attribution.py`, reusing `simulate()`/`scores()`/`load_matrices()`/`align_to()`
unchanged (no new strategy, no new fetch beyond `probe_macro_analog.py`'s already-cached NIFTY/
India-VIX series) — IBS(5) top_k=5 lag=1 on the standing universe. Four regime variables, each
already used somewhere in this project (NIFTY trend vs its 150d SMA — the Sixty-first entry's own
gate; realized vol of NIFTY, 21d annualized; India VIX level — the fear-buy entries; this
universe's own breadth, fraction of stocks above their own 200d SMA — the Hundred-and-first entry's
Zweig probe), each computed at the ranking date of every real month with the project's standard
no-lookahead `align_to`/rolling convention, tercile-split on the FULL history (so a bucket's
definition doesn't depend on which months land in it). For each bucket: is the strategy's own mean
return different from what buying `top_k` RANDOM eligible stocks in the EXACT SAME months would
have earned (1,500-seed control) — this isolates whether a regime explains the STOCK-SELECTION edge
specifically, since every bucket's random control already prices in that regime's own market beta.

**Only one of the four regime variables replicates across both universes, and it's the most
basic one: realized volatility.** WIDE_UNIVERSE: vol21 high tercile p(random>=actual)=0.5503 (no
edge over random at all — the strategy is statistically indistinguishable from random
stock-picking in the highest-vol months), vol21 mid p=0.0133 (a real, strong edge). UNIVERSE_B:
vol21 high p=0.5583, vol21 mid p=0.0153 — both numbers reproduce closely on a disjoint 54-stock
universe. **The other three variables do NOT replicate** — the India VIX mid-tercile cell looked
like the single strongest result on WIDE_UNIVERSE (p=0.0087) and flips to p=0.6722 (no edge at all,
direction reversed) on UNIVERSE_B; breadth's mid-tercile edge (p=0.0253 on WIDE_UNIVERSE) likewise
disappears (p=0.1306) on UNIVERSE_B; the NIFTY trend split is weak and inconclusive on both
(p=0.05-0.29, no bucket clears an uncorrected 0.05 on either universe). Registered, not credited —
exactly the single-universe mirage this project's own standing practice exists to catch (the
Sixty-eighth/Hundred-and-fourth/Hundred-and-fifth entries all found a result that looked real on one
universe and reversed or vanished on the other).

**What the replicating finding actually says, read against this project's own history of drawdown-
overlay attempts.** Mean monthly return in the high-vol tercile isn't bad on its own (+1.88%/+1.89%
on the two universes — among the better buckets by raw average) — what vanishes there isn't
profitability, it's the STOCK-SELECTION skill specifically: in the calmest and most turbulent
thirds of months, "most oversold" performs statistically like a random pick from the same universe;
the real edge over random concentrates in the MIDDLE third. The worst single month in this entire
backtest (2020-02-28, -30.0%/-24.6% on the two universes) sits in the high-vol tercile, consistent
with this being a high-VARIANCE bucket (both the best and worst outcomes cluster there) rather than
a uniformly-bad one, which is also why a hard regime GATE on this axis was never tried by any prior
entry and wouldn't obviously help — cutting high-vol months removes upside and downside together,
the same "costs more return than it saves" shape the ATR-stop and trend-gate overlays already
showed on a different conditioning variable. This may explain, without proving, why those three
overlays (gated on price TREND, not realized VOL) kept failing to replicate: they were conditioning
on a variable this entry's own cross-universe check says doesn't discriminate skill from noise,
while the one variable that does (realized vol) has never been used as a gate.

**Net verdict.** Not a new mechanism and not a new overlay — a diagnostic entry answering "what
regime distinguishes a good IBS-rotation month from a bad one" directly, rather than inferring it
backward from which overlays happened to help. One real, cross-universe-replicated finding (the
strategy's stock-picking skill is regime-dependent on realized volatility specifically, strongest
in moderate-vol months, statistically absent in the highest-vol tercile) and three single-universe
mirages, registered for honesty and explicitly not credited. 22 p-values registered in
`multiple_comparisons.py`; honest family now 537 registered + 508 unregistered = m=1045,
Bonferroni threshold 0.00005 — none of the 22 comes within two orders of magnitude of it, consistent
with every other result in this project's ledger. 279 tests pass (unchanged — no new logic needing
a test; the new probe script reuses already-tested `simulate()`/`scores()`/`align_to()` verbatim,
same convention every other standalone probe in this project follows). Concrete next step, not done
here: a realized-vol-tercile GATE (hold cash, or at minimum skip the lowest-conviction picks, in the
highest-vol tercile specifically) through the same four-check battery (screening, quarter-split,
survivorship stress, cross-universe replication) the three price-trend overlays already got —
untested, because this entry's job was to find which variable is worth gating on, not to build the
fourth overlay. **IBS rotation remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-eighth: two book-sourced signals never tested here — frog-in-the-pan momentum
quality and the Ichimoku cloud — a clean null and a single-universe mirage

Requested direction: search books for strategies not yet tried and apply them to the data already
collected. Two genuinely new signal constructions, both added as `kind` branches to
`probe_reversal_rotation.py`'s existing `scores()` dispatcher (the same minimal-diff pattern Entry
87 used for realized skewness) — no new file, no new fetch, full reuse of `simulate()`'s lag-1
fill, cost model, and random-portfolio control.

**Frog-in-the-pan / momentum quality** (Da, Gao & Jagadeesh 2014, "Information Discreteness and
the Cross-Section of Stock Returns"; described as a retail-accessible factor in Wesley Gray &
Jack Vogel's book "Quantitative Momentum"): a 12-1 momentum stock that got there via many small
same-sign daily moves continues more reliably than one that got there via a few big jumps.
Score = `-(12-1 momentum) x (fraction of up days over the same formation window)`, so a
positive-momentum, smooth-path name sorts first (most negative score, this file's ascending-pick
convention); a negative-momentum name's score stays positive regardless of its own path
smoothness, so the ranking's head is naturally restricted to positive-momentum names without a
separate filter.

**Ichimoku cloud distance** (Hosoda's Ichimoku Kinko Hyo, as described in John Murphy's
"Technical Analysis of the Financial Markets" — one of the most widely used indicators in retail
technical analysis, and, like Bollinger Bands/Stochastic before it, never tested in this project
despite that popularity): Score = `-(close - today's cloud midpoint) / close`. The cloud "visible"
at today's close is Senkou Span A/B computed from data through 26 days ago (it's plotted 26
periods ahead of its own inputs in the standard construction), so reading it via a 26-day forward
shift of the already-computed span uses no lookahead — the stock furthest above its own current
cloud sorts first, the same "distance above a bullish reference level" shape as `hi52`'s 52-week-
high proximity, with Ichimoku's own specific construction instead.

Pre-registered: both signals x top_k in {3, 5, 8}, IBS rotation's own 52-stock `WIDE_UNIVERSE`,
lag-1 fill, 1,500-seed random-portfolio control — `--frog`/`--ichimoku` in
`probe_reversal_rotation.py`. Checked on `UNIVERSE_B` before trusting anything, per this project's
own standing practice.

**Frog-in-the-pan: a clean null on both universes.** WIDE_UNIVERSE: 2.35-5.75%/yr vs random
10.55-11.03%/yr at every top_k (p=0.86-0.92 — the strategy loses to most random draws), Q4
strongly negative at every size (-34% to -50%) — the same basket-wide-momentum decay signature
the Sixty-third entry already found for plain 12-1 momentum and 52-week-high. UNIVERSE_B is
directionally better (p=0.10-0.37) but never clears even an uncorrected 0.05. The "quality" filter
doesn't rescue momentum here, the same conclusion the Twenty-eighth entry reached testing whether a
regime filter rescues Bollinger Bands: a filter on top of a mechanism with no real edge underneath
doesn't manufacture one.

**Ichimoku is the mirage this entry exists to report.** WIDE_UNIVERSE: a clean null at every
top_k (9.94-12.68%/yr vs random 12.80-13.21%/yr, p=0.47-0.78). **UNIVERSE_B: top_k=3 clears
p=0.0360, top_k=5 clears p=0.0286**, both walk-forward halves strongly positive (+124% to +190%
across the two halves) — looks like exactly the kind of result this project would normally chase
further. But the discovery universe (`WIDE_UNIVERSE`) shows nothing at all for the same rule, same
top_k values, same window — the identical single-universe-disagreement shape the Hundred-and-
seventh entry's own India-VIX and breadth splits just showed. Per this project's own standing
rule (a candidate only counts once it clears the SAME bar on both independent universes, not
either one alone — the rule that already sank SuperTrend's `CL=F`, IBS's FX retest, and 52-week-
high's widening), **Ichimoku rotation is rejected, not flagged as promising.** Also worth naming:
`UNIVERSE_B`'s own Q4 is negative at top_k=5/8 (-15%, -6%) even within the universe where it
"passed" — only top_k=3 has all four quarters positive there, so even the passing side of this
mirage isn't uniformly clean.

**Net verdict.** Two new, literature-sourced constructions (one momentum-adjacent, one a classic
multi-component technical indicator), added at minimal cost by reusing the existing `scores()`
dispatcher rather than a new probe file, both rejected — one on a clean uniform null, one on
exactly the cross-universe-disagreement pattern this project's own methodology exists to catch. No
quarter-split/perturbation/survivorship-stress follow-up run on either (per the establishing
"stop at the first clear crack" practice — Ichimoku already fails the cross-universe bar outright,
frog is uniformly negative). 12 p-values registered in `multiple_comparisons.py`; honest family
now 549 registered + 508 unregistered = m=1057, Bonferroni threshold 0.00005 — none of the 12
comes close, consistent with the rest of this project's ledger. 279 tests pass (unchanged — no new
logic needing a test beyond what `scores()`'s existing branches already exercise; the two new
branches reuse `simulate()`'s already-tested machinery verbatim). **91 mechanisms tested; IBS
rotation remains the sole standing finding; nothing is declared tradable.**


## Hundred-and-ninth: Alexander's Filter Rule, tested with Aronson's own methodology — a
block-bootstrap Reality-Check correction, built as new infrastructure, and a clean null on
both markets even before the correction is applied

Direct follow-up to "apply the strategies in the book": Aronson's "Evidence-Based Technical
Analysis" isn't really a strategy cookbook — its core contribution is the METHODOLOGY for
telling a real technical-rule edge from a data-mined one, specifically a bootstrap-based
correction (the same idea as White's "Reality Check"/Hansen's SPA test, which the book cites) for
the bias of searching a grid and reporting only the best cell. This project already has Bonferroni/
Benjamini-Hochberg (`multiple_comparisons.py`) for correcting a family of ALREADY-COMPUTED
p-values after the fact, but nothing that directly simulates "how good does the best-of-many-
configs look under a null with no real exploitable structure" — which is a different, often less
conservative (Bonferroni assumes independence; a bootstrap respects the grid's actual correlation
structure) correction, and the one Aronson's own book is specifically about.

**The rule**: Alexander's (1961) Filter Rule — long when price is x% above its own most recent
trough (since it last went flat), flat when x% below its own most recent peak (since it last
went long). One of the oldest technical rules academics have tested, predating everything else in
this project's `probe_*.py` lineage by decades, and genuinely distinct from every trend-following
construction already here (not a fixed-N-day channel like Donchian, not an ATR-ratchet band like
SuperTrend, not a moving-average cross).

**The correction**: `probe_filter_rule.py`'s block-bootstrap — resample 21-day blocks of the REAL
daily-return series (with replacement) into a synthetic price path of the same length (preserves
local, within-block serial dependence; destroys the specific long-range trend/cycle structure of
the one real historical path), rerun the WHOLE pre-registered grid (x in {1,2,3,4,5,7.5,10,15,20,
25}%) on every synthetic path, and track the single best cell's performance each draw. The actual
best cell's percentile within that "best-of-grid under no real structure" distribution is the
corrected p — by construction always at least as conservative as testing the chosen cell alone
against its own null (the "naive" p also reported, for the contrast Aronson's book makes explicit).
No lookahead: the filter state decided through `close[i]` fills at `close[i+1]` and only starts
earning returns from `close[i+1]` onward, this project's standing lag-1 convention, verified by a
synthetic all-up-then-reversal sanity check before any real number was trusted.

**Result: a clean null on both markets, and the correction barely has to do any work because the
NAIVE test already fails.** NIFTY (10y, 0.1%-per-leg ETF-level cost): best cell x=5% at 10.87%/yr,
15.1% max drawdown — naive p=0.2598, Reality-Check-corrected p=0.5503. S&P 500 (20y): best cell
x=25% at 10.32%/yr — naive p=0.2072, corrected p=0.3185. Neither market's single best grid cell
clears even an uncorrected 0.05 tested alone, let alone once the search-over-10-configs bias is
priced in. Descriptively, NIFTY's mid-range cells (x=4/5/7.5%) decay toward a flat or negative Q4
(-3% to +3%) while S&P's equivalent cells show the opposite, strengthening into Q3/Q4 (+56% to
+161%) — neither pattern is statistically meaningful here since the underlying result never clears
significance in the first place, but it's a reminder that "looks clean on quarter-split" and "beats
a proper data-mining-bias-corrected null" are different bars, and this result fails the second one
before the first one is even worth checking carefully.

**Net verdict.** The book's actual contribution — applied directly rather than cherry-picking one
of its cited rules — doesn't change this project's standing verdict (one of the oldest technical
rules in the literature, tested honestly, is still a null here), but it adds a genuinely new,
reusable correction tool (`block_bootstrap_price()`, a block-bootstrap Reality-Check harness
generic enough to rerun on any future grid-searched single-instrument rule in this project, not
just the filter rule) that this project's existing Bonferroni/BH machinery didn't cover. 4 p-values
registered in `multiple_comparisons.py` (naive and corrected, both markets); honest family now 553
registered + 508 unregistered = m=1061, Bonferroni threshold 0.00005 — moot here since neither
market's naive p is anywhere close to 0.05 either. 279 tests pass (unchanged — a standalone probe
script, no pytest file, per this project's own established convention; correctness checked via the
script's own synthetic sanity check instead). **91 mechanisms tested; IBS rotation remains the sole
standing finding; nothing is declared tradable.**
