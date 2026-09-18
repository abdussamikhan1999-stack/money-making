# money-making — Highest Open / Lowest Open, on Kite

An intraday reversal strategy — "Highest Open / Lowest Open" — wired up to
Zerodha's Kite Connect API. Backtest it, paper-trade it, and only then, if
you choose to, let it place real orders.

**⚠️ This is not financial advice and there is no guarantee this makes
money.** The source idea (a ForexFactory thread) is presented there for
educational purposes only, with an explicit warning to check with an
accredited financial specialist before risking real capital. Backtest and
paper-trade thoroughly before ever running this live. You are solely
responsible for any trades it places.

## Daily-bar strategies (a second family, ten mechanisms so far)

`backtest_daily.py` backtests daily-bar strategies against years of data
instead of intraday's 60-day cap, via
`--strategy {donchian,rsi2,threebar,squeeze,volume,turtlesoup,macd,bollinger,high52w,overnight}`:

```
python backtest_daily.py --strategy donchian --symbol '^NSEI' --period 10y --walk-forward
python backtest_daily.py --strategy rsi2 --symbol RELIANCE.NS --period 10y --walk-forward
python backtest_daily.py --strategy threebar --symbol SBIN.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy squeeze --symbol INFY.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy volume --symbol GC=F --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy turtlesoup --symbol AXISBANK.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy macd --symbol TCS.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy bollinger --symbol RELIANCE.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy high52w --symbol TCS.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy overnight --symbol TCS.NS --period 10y --walk-forward --commission-per-trade 20
```

**`donchian`** — the "Turtle Trading" entry rule (N-day high/low channel
breakout). Systematic testing (18 instruments, parameter perturbation,
quarter-splits) found smooth, all-positive performance across every tested
parameter on BTC-USD/ETH-USD — but crypto isn't tradable via Kite at all,
and quarter-splitting shows that result is concentrated in crypto's
2016-2021 bull run, decayed to roughly flat in the most recent 2.5 years.
Kite-tradable candidates (gold, oil) are considerably weaker once
regime-split.

**`rsi2`** — Larry Connors' 2-period RSI mean-reversion strategy (a
published, widely-cited system: 200-day trend filter, RSI(2) extremes for
entry, 5-day SMA for exit). The strongest, most theoretically coherent
result of the whole project — an 8/14 hit rate on REAL Kite-tradable
instruments (Nifty, Bank Nifty, 5 large-cap stocks, oil), smooth parameter
sensitivity with a sensible causal story (only genuinely extreme RSI
readings work, matching Connors' own thesis). **But the magnitude is too
small to be worth trading at any risk setting**: 0.04-0.25%/year at
standard sizing, and even at 10% risk per trade (reckless), `^NSEI` only
reaches 0.83%/year with 19% drawdown — return and drawdown scale together,
so no amount of leverage fixes a genuinely thin per-trade edge. See
`CLAUDE.md` for the full breakdown, including two real bugs found and
fixed along the way (a fake 100% win rate from a units mismatch, and a
never-reset daily-loss breaker that silently truncated trading after ~4
losses in any multi-year run).

`rsi2`'s exits can optionally route through an ATR-scaled profit-booking
overlay (`--breakeven-atr-mult` / `--trail-atr-mult`, reusing
`strategy.py`'s `TrailingStopManager`) — tested specifically to see if
locking in profit earlier fixes RSI-2's thin-magnitude problem. **It
doesn't**: it made every tested instrument worse (see CLAUDE.md), because
RSI-2's own exit rule already functions as a profit target for its
mean-reversion mechanism. Off by default (`None`/`None`); left in the code
as a generic tool for a future strategy whose entry has no natural
profit-taking exit of its own.

**`threebar`** — a 3-bar range-compression breakout/continuation pattern
sourced from a ForexFactory thread (see CLAUDE.md for the exact thread
citations): two prior bars closing near the same level, then a third
closing decisively beyond both, with a fixed R-multiple take-profit. The
best instrument hit rate in the project after RSI-2 (6/12 Kite-tradable
instruments passed walk-forward screening) with genuinely stable
quarter-split results on 5 of those 6. At standard 0.5% risk-per-trade the
edge is thin (0.4-2.2%/year) like RSI-2 — but unlike RSI-2, a modest bump
to 1% risk-per-trade (still conservative) turns this into 1.9-4.6%/year
across five independent instruments at 1-4.4% max drawdown, without the
"return and drawdown just scale together" problem that made leverage
useless for RSI-2. Still requires per-instrument selection (half the
instruments tested show no edge) and one passer (`INFY.NS`) leans heavily
on a single COVID-era quarter. See CLAUDE.md's "Twelfth" section for the
full breakdown.

**`squeeze`** — John Carter's TTM Squeeze, reproducing LazyBear's widely-
forked open-source "Squeeze Momentum Indicator" exactly (Bollinger Bands
contracting inside Keltner Channels = "squeeze on"; BB expanding back out =
"squeeze fires", trade the linear-regression momentum's sign). A genuinely
different, volatility-state-timing mechanism — but only 3/12 Kite-tradable
instruments passed walk-forward screening, a 25% hit rate right at the
"pure chance" level already established as disqualifying in this project
(see the sector sweep's 18%). One passer (`INFY.NS`) looks clean on
quarter-split and reasonable on perturbation, and shares the "1% sizing
roughly doubles the return" property the 3-bar breakout has — but surfacing
from a chance-level sweep means it isn't distinguishable from a lucky draw.
See CLAUDE.md's "Fifteenth" section for the full breakdown.

**`volume`** — Chaikin Money Flow + On-Balance Volume confirmation,
reproducing `XBT3K/VOLUME-ALGO-EURUSD`'s open-source `VolumeOBVCMF`
backtrader strategy: long when both CMF and a (windowed) OBV are positive,
exit when both turn negative. The first strategy in this project to use
volume at all rather than price alone — and correctly never trades index
symbols (`^NSEI`, `^NSEBANK`), since yfinance reports no real volume for an
index. Screened on 10 stocks/futures: 3/10 passed walk-forward (`INFY.NS`,
`WIPRO.NS`, `GC=F`), but quarter-split shows `INFY.NS`/`WIPRO.NS` both
decaying in the most recent quarter — only `GC=F` (gold) is clean, with a
smooth, all-positive perturbation sweep on par with RSI-2's. **Sizing
doesn't rescue it**, though: unlike the 3-bar breakout or Squeeze, raising
risk-per-trade just drawdown-halts the run instead of scaling the return,
leaving `GC=F` stuck at ~1.30%/year — real but too thin, the same verdict
as RSI-2. See CLAUDE.md's "Sixteenth" section for the full breakdown.

**`turtlesoup`** — Linda Raschke's "Turtle Soup" failed-breakout fade: the
exact counter-trend inverse of `donchian`'s own entry trigger (buy a failed
new N-day low, sell a failed new N-day high, once price closes back inside
the prior range). Screened on the same 12-instrument set: 3/12 passed
walk-forward (`^NSEI`, `ITC.NS`, `AXISBANK.NS`) — 25%, chance-level again.
`AXISBANK.NS` is clean on quarter-split (all 4 quarters positive) and has
the smoothest single-survivor perturbation sweep in the whole project (10
tested parameter values, all positive on both halves, no cliffs) —
interesting since the same stock failed SuperTrend's own perturbation
sweep on a single-point-fit cliff. Sizing helps up to 1% risk (1.71%/year,
1.1% drawdown) then drawdown-halts at 2%+ — thin, same bucket as
SuperTrend's `CL=F` and volume's `GC=F`, not confirmed beyond one
instrument. See CLAUDE.md's "Twentieth" section for the full breakdown.

**`macd`** — Gerald Appel's MACD crossover, one of the most widely used
technical indicators anywhere: buy when the MACD line (fast EMA - slow EMA
of closes) crosses above its own signal line (an EMA of the MACD line),
short on the reverse. Screened on the same 12-instrument set: only **2/12
passed** (`TCS.NS`, `WIPRO.NS`) — 16.7%, the lowest hit rate of any
indicator-based strategy in this project. `WIPRO.NS` decays negative in
the most recent quarter; `TCS.NS` is clean (all 4 quarters positive) with
reasonable (not perfect) perturbation robustness. Sizing helps up to 1%
risk (1.73%/year, 2.6% drawdown) then drawdown-halts at 2%+ — thin, same
"real but too small" bucket as most of this project's survivors. See
CLAUDE.md's "Twenty-first" section for the full breakdown.

**`bollinger`** — John Bollinger's own classic bands: buy a close below a
rolling SMA-±-stdev lower band, short above the upper band, exit at the
middle band. Screened on the same 12-instrument set: **0/12 passed** — the
worst hit rate of any strategy in this project (below MACD's 16.7% and
the sector sweep's 18% floor), with two instruments (`RELIANCE.NS`,
`TCS.NS`) drawdown-halting outright. No perturbation needed given how
uniform the failure is. **A follow-up regime filter** (`--bb-trend-
filter-lookback`/`--bb-trend-filter-atr-mult`, skip entries while the
market is trending rather than range-bound — the same shape as RSI-2's
own trend filter, which made RSI-2 this project's most robust result)
didn't rescue it either — still 0/12 at the default setting, and 0/5 on a
subset across 4 tightness values. RSI-2's filter works because its
underlying signal is real; Bollinger's band-touch entry doesn't appear to
be, filtered or not. See CLAUDE.md's "Twenty-seventh"/"Twenty-eighth"
sections for the full breakdown.

**`high52w`** — George & Hwang's 52-week-high proximity momentum (a real,
decades-documented academic anomaly, Journal of Finance 2004): long when
today's close is within 5% of its trailing 252-day high, not a fresh
extreme like `donchian`, just NEAR a recent one. The best hit rate since
3-bar breakout — 4/10 tradable instruments passed walk-forward
(`^NSEI`, `TCS.NS`, `AXISBANK.NS`, `GC=F`; `^NSEBANK` excluded, it can't
size even one unit at Bank Nifty's price level, a known index-sizing
artifact, not a strategy failure). Quarter-split narrows this to two
genuinely clean survivors (`TCS.NS`, `GC=F`) after dropping `AXISBANK.NS`
(hollow consistency — 3 of 4 quarters actually negative) and flagging
`^NSEI` (recent-quarter decay). Perturbation on the two survivors is the
smoothest sweep in this project since 3-bar breakout — all-positive on
both walk-forward halves across every tested `entry_threshold`/
`lookback_period`/`exit_threshold` value bar one. Sizing helps up to 1%
risk (`TCS.NS` 1.31%/yr, `GC=F` 1.99%/yr) then drawdown-halts at 2% — real
but thin, the same bucket as Turtle Soup/SuperTrend/MACD. See CLAUDE.md's
"Thirty-fifth" section for the full breakdown.

**Widen-and-check on `high52w` (Thirty-sixth entry, `probe_high52w_widen.py`)
does NOT strengthen the above finding** — it reveals the original 40% hit
rate was a 2-way-split artifact. On `probe_pead.py`'s existing 40-stock
NSE large-cap universe, walk-forward alone still passes 15/40 (37.5%,
looks like clean replication), but a full 4-way quarter-split — the same
check that caught `AXISBANK.NS`'s hollow consistency above — leaves only
2/15 (13%) with all 4 quarters positive: `TCS.NS` (reproduces its
original numbers almost exactly) and one new name, `NESTLEIND.NS`. 2 of
40 (5%) is at or below every chance-level band this project uses to
disqualify a result. Closed the same way PEAD and SuperTrend's oil
survivor were closed. See CLAUDE.md's "Thirty-sixth" section.

**`overnight`** — the overnight-return anomaly (Lou, Polk & Skouras 2019;
Cliff, Cooper & Gulen 2019): decomposes a bar's return into overnight
(yesterday's close → today's open) vs. intraday pieces, something no
other strategy here does. Enters at today's close on trailing overnight-
return momentum, exits at TOMORROW's open — a genuine single-bar
overnight-only hold, which needed one small opt-in engine flag
(`exit_at_open`) in `simulate_daily()`, unchanged for every other
strategy. Screened on the full 40-stock universe from the start (the
lesson the two entries above just taught the hard way): **0/40 passed
walk-forward**, net-negative everywhere at realistic cost. But a
zero-commission recheck on 3 names came back clearly gross-positive, and
sweeping the entry threshold to cut trade frequency shrinks the loss
monotonically without ever crossing into profit — the gross edge is real
but averages roughly ₹3-4/trade, an order of magnitude under the ₹20
round-trip cost used throughout this project. Closed for a new reason:
not decay, not a lone survivor, not a capital wall, but a real edge too
small in magnitude to ever clear realistic transaction costs. See
CLAUDE.md's "Thirty-seventh" section for the full breakdown.

**Monthly cross-sectional IBS rotation (Thirty-eighth entry,
`probe_ibs_rotation.py`)** — the strongest result in this project's
history: ranks all 40 universe stocks by trailing 5-day average IBS each
month-end, goes long the 5 most-oversold, equal-weighted, rebalanced
monthly. Full period 15.51%/yr at 26.6% drawdown vs. `^NSEI`'s 10.21%/yr
at 38.4% — beats the benchmark on both axes. Walk-forward positive both
halves; **quarter-split all 4 quarters positive, reproduced across 5
different parameter configs**; 16/16 perturbation cells positive, no
cliffs; 30/40 stocks individually net-positive with only 31% concentration
in the top 3 (far broader than any prior survivor here). A 200-seed
random-5-stock control check — the first of its kind in this project —
shows the result at the 86.5th percentile (z≈1.06): real and persistent,
but not an overwhelming statistical outlier. Stays a probe script (not
ported into `daily_strategy.py`'s single-instrument architecture — this is
portfolio-level, not single-instrument). Recommended next step: paper-track
this rule forward before considering real capital. See CLAUDE.md's
"Thirty-eighth" section for the full breakdown, including a calendar-tie
reproducibility bug caught and fixed along the way.

**Stress test (Thirty-ninth entry, `probe_ibs_rotation_widen.py` /
`probe_ibs_rotation_significance.py`)** — widened the universe to 52 stocks
(40 large-cap + 12 small/mid-cap) and the result got STRONGER, not
weaker: 22.11%/yr at 38.2% drawdown, all 4 quarters positive across 6
parameter configs, top-3 concentration improved to 26.5%. A 1,500-seed
random-control test (up from 200) now clears conventional significance at
three portfolio sizes (`top_k=3/5/8`: p=0.037/0.015/0.014). First
cross-sectional finding in this project to survive widening rather than
collapsing (PEAD and 52-week-high both fell apart on the same test). One
real caveat found: at the LOW end of this project's ₹30,000-100,000
target capital range, per-position notional (₹6,000 at ₹30,000 capital)
can't buy a whole share of the priciest names — fine at ₹100,000, real
share-rounding fixed in the Fifty-fourth entry. Still not declared
tradable — survivorship bias unresolved, genuine forward out-of-sample
data still the one check no backtest can run. See CLAUDE.md's
"Thirty-ninth" section for the full breakdown.

**Survivorship-bias stress test (Fortieth entry,
`probe_ibs_rotation_survivorship.py`)** — a true point-in-time
constituents fix isn't available (yfinance has no historical-membership
API, and two real delistings, `DHFL.NS`/`RELCAPITAL.NS`, return zero rows
— fully invisible, no fix possible). Partial check instead: added four
other real NSE catastrophic collapses that never stopped trading
(`JETAIRWAYS.NS`, `YESBANK.NS`, `RCOM.NS`, `PCJEWELLER.NS`, all -97% to
-99% from peak) to the 52-stock universe (56 total) and reran everything.
Result went UP, not down: 23.95%/yr, all 4 quarters positive, reproduced
across 3 more configs. But three of the four blowups became **top-3
contributors** (`PCJEWELLER.NS` the single best contributor in the whole
universe), pushing concentration to 50.4% — because monthly rebalance
means the strategy catches one-month bounces after a crash rather than
holding through the full collapse. Reframes the risk: this edge is partly
a high-volatility-mean-reversion harvest, not purely a diversified
oversold-quality-stock pick — a stock in freefall when picked could just
as easily keep falling. Also flagged: rerunning the identical default
config produced a different concentration number (44.0% vs. 50.4%) run to
run, consistent with this project's known intermittent yfinance retry
behavior — the pass/fail quarter-split result held both times, but exact
concentration figures are noisy at the single-run level. Still not
declared tradable. See CLAUDE.md's "Fortieth" section for the full
breakdown.

**Forward paper-tracking (Forty-first entry, `paper_track_ibs_rotation.py`)**
— every entry above recommended it and none had done it: real out-of-sample
data no backtest can fabricate. Implements the Thirty-ninth entry's live
rule exactly (`top_k=5, lookback=5`, monthly, the 52-stock `WIDE_UNIVERSE`,
₹100,000 capital, standard cost model). Run it once a month; it marks the
previous month's picks to market and logs the next month's picks to
`paper_track_ibs_rotation_log.json`, an append-only record — **do not
hand-edit it**. First real record logged 2026-09-17. See CLAUDE.md's
"Forty-first" section for the full writeup.

**Does the recipe generalize beyond IBS? (Forty-second entry,
`probe_rsi2_rotation.py`)** — tested whether the cross-sectional-monthly-
rank SHAPE, not something IBS-specific, is what works, by swapping in
Connors RSI(2) (this project's other well-known survivor) as the ranking
signal on the same 52-stock universe. Walk-forward and quarter-split both
pass at all 3 tested portfolio sizes (17.11%/15.43%/19.29%/yr at
top_k=3/5/8, all 4 quarters positive at every size) — the recipe does
produce a real, backtest-passing shape on a second signal. But the
1,500-seed significance test is meaningfully weaker: only `top_k=8` clears
conventional significance (p=0.025); `top_k=3`/`top_k=5` (p=0.198/0.285)
don't, versus IBS rotation clearing all three (p=0.037/0.015/0.014).
Partial generalization — IBS rotation remains the stronger, more
rigorously corroborated finding. See CLAUDE.md's "Forty-second" section
for the full breakdown.

**Does the recipe generalize to momentum/breakout signals too? (Forty-third
entry, `probe_breakout_rotation.py`)** — completed the survey with 3-bar
compression breakout and Turtle Soup (this project's other two real-but-thin
survivors) reshaped into the same monthly cross-sectional rank. Both fail
outright at walk-forward (`threebar`: in-sample +22.68%/yr, out-of-sample
-3.63%/yr; `turtlesoup`: +19.41%/yr in-sample, -0.43%/yr out-of-sample,
both INCONSISTENT), unlike IBS/RSI-2 which both passed. The recipe works
for this project's mean-reversion signals, not its momentum/breakout ones
— see CLAUDE.md's "Forty-third" section for the likely structural reason
why.

**Does adding a short leg cut the long-only version's drawdown? (Forty-fourth
entry, `probe_ibs_rotation_longshort.py`)** — tested whether shorting the
most-OVERBOUGHT names by the same IBS rank (mirroring the long side's
most-oversold picks) hedges out the long-only version's NIFTY beta and
cuts its 38.2% max drawdown. It does cut correlation to NIFTY sharply
(0.796 → 0.044) and drawdown modestly (38.2% → 33.5%) — but at the cost of
destroying nearly the entire return (22.12%/yr long-only → -0.19%/yr
long/short, walk-forward INCONSISTENT). The short leg alone lost money on
its own (61.7% of months the "overbought" names kept rising rather than
reverting), almost exactly offsetting the long leg's gain — IBS's real
edge is one-sided, not a symmetric mean-reversion signal. Also flagged: a
month-long short isn't directly executable in NSE cash equity anyway
(needs SLB or futures, neither modeled) — see CLAUDE.md's "Forty-fourth"
section for the full caveat and numbers.

**Does a targeted NIFTY-futures beta hedge do better? (Forty-fifth entry,
`probe_ibs_rotation_hedged.py`)** — the Forty-fourth entry's per-stock
short leg fought IBS's real one-sided edge and lost. This instead leaves
every stock pick untouched and only shorts NIFTY futures, sized to the
long-only strategy's own regression beta (1.143, cross-checks against
entry 44's 0.796 correlation). It works cleanly as a backtest: full hedge
cuts drawdown 38.2%→25.5% while keeping a real 8.19%/yr return,
walk-forward consistent, all 4 quarters positive. But NIFTY futures are
blocked by the same capital-tier wall as every other Nifty derivative in
this project (Sixth/Seventh/Ninth entries): one lot (65 units, confirmed
live) needs ~₹196,800 margin at current NIFTY levels — 2-6.5x this
project's whole ₹30,000-100,000 target capital range, before even sizing
the beta-appropriate fraction of a lot. Real backtest, not actionable at
this project's scale. See CLAUDE.md's "Forty-fifth" section for the full
numbers.

**Does substituting a NIFTYBEES ETF short for NIFTY futures route around
the capital wall? (Forty-sixth entry, `probe_ibs_rotation_etf_hedge.py`)**
— yes, and it finds a better hedge than either prior attempt. NIFTYBEES
(confirmed live, NSE `EQ` segment, ordinary whole-share sizing, no fixed
lot) is capital-feasible at both ends of this project's ₹30,000-100,000
range. A FULL beta-hedge underperforms badly (1.61%/yr, walk-forward
INCONSISTENT) because the ETF's total return runs ~32 percentage points
ahead of the price-only `^NSEI` over 10 years (real dividend accrual, not
a bug) — shorting it bleeds that extra drift. A HALF hedge (0.5x) avoids
most of that drag: 12.01%/yr at 24.2% max drawdown, walk-forward
consistent, all 4 quarters positive — better return than the futures full
hedge (8.19%/yr) at comparable drawdown reduction, and the first Nifty-beta
hedge in this project to be both rigorous AND actually tradable at this
project's capital scale. See CLAUDE.md's "Forty-sixth" section for the full
comparison table and the dividend-drag mechanism.

**Is the 0.5x hedge ratio robust, or a lucky single point? (Forty-seventh
entry)** — swept the ratio on a finer grid (0.25 to 1.0). Return declines
smoothly and monotonically as the ratio rises (17.11%/yr at 0.25 down to
1.60%/yr at 1.0); drawdown reduction is a smooth U-shape bottoming around
0.50-0.625 (24.2%/23.6%) before rising back up as dividend drag reasserts
itself. Every ratio from 0.25 through 0.875 passes cleanly (walk-forward
consistent, all 4 quarters positive) — consistency only starts degrading
at 0.90 (3/4 quarters) and fully breaks at 1.0 (walk-forward inconsistent,
2/4 quarters), matching the Forty-sixth entry's own full-hedge finding.
**0.50 is not a single-point fit** — it sits well inside a genuinely
robust 0.25-0.875 range, at the point closest to minimum drawdown before
return decays too far. See CLAUDE.md's "Forty-seventh" section for the
full sweep table.

**Forward paper-tracking for the hedged strategy (Forty-eighth entry,
`paper_track_ibs_rotation_hedged.py`)** — a second, parallel live record
alongside the Forty-first entry's unhedged tracker, since the half-hedge
(entries 46-47) is a materially different, also-real risk profile worth
its own out-of-sample record. Same monthly cadence, same picks as the
unhedged tracker, plus a short `NIFTYBEES.NS` leg at `hedge_ratio=0.5`,
beta recomputed fresh each run rather than hardcoded. First record logged
2026-09-17. **Two independent forward logs now run in parallel** —
`paper_track_ibs_rotation_log.json` (unhedged) and
`paper_track_ibs_rotation_hedged_log.json` (half-hedged) — both should be
rerun monthly; neither should be hand-edited. See CLAUDE.md's
"Forty-eighth" section for the full writeup.

**Bug fix: live-tracker picks were nondeterministic during market hours
(Forty-ninth entry)** — the Forty-eighth entry's writeup wrongly blamed
the two trackers' same-day pick mismatch on generic "yfinance retry
variance." The real cause: both trackers ran while NSE was still open,
ranking against yfinance's still-forming "today" daily candle, whose
high/low/close keep changing intraday — not a missing-symbol retry issue
at all. Fixed with a shared `latest_settled_date()` guard in
`probe_ibs_rotation.py`, used by both live trackers, that falls back to
the prior trading day until 15:45 IST. See CLAUDE.md's "Forty-ninth"
section for the full writeup.

**Composite IBS + low-volatility score, tried and rejected (Fiftieth
entry, `probe_ibs_lowvol_composite_rotation.py`)** — a third way of
combining independently-real signals (after AND-gating one trade's
entry/exit, and running a diversified multi-strategy portfolio, both of
which hurt in earlier entries): blend IBS and the low-volatility anomaly
into one cross-sectional rank score instead of ranking by IBS alone. At
matched `top_k`, it never beats plain IBS on a full-period sweep, collapses
out-of-sample in walk-forward (a candidate that looked competitive
in-sample gives up ~60% of its return out-of-sample vs. pure IBS), and
loses statistical significance entirely at a plausible middle weight
(p=0.36 vs. pure IBS's p=0.015 on the same universe/top_k). Blending in
volatility reintroduces that factor's own known recent-quarter decay
rather than adding a "free" risk reduction. See CLAUDE.md's "Fiftieth"
section for the full numbers.

**Adversarial council review (Fifty-first entry)** — three independent
reviewers (statistical rigor, data/execution realism, red-team) audited
the IBS rotation finding and converged on a harder verdict than this
project's own "not tradable yet" caution implied: the reported
significance is a single-config p-value with no multiple-comparisons
correction across ~25+ internal variants and 50+ mechanisms tried, no
slippage/spread term existed in the cost model, and the Fortieth entry's
survivorship "pass" is really evidence of bounce-harvesting, not against
it. No reviewer would deploy capital on this basis.

**Closing the slippage gap (Fifty-second entry, `probe_ibs_rotation.py` /
`_widen.py` / `_significance.py`)** — added a symmetric per-leg
`slippage_pct` fill-price haircut (default 0.0, old behavior unchanged) on
top of the existing STT/stamp/DP cost model. Swept 0-0.5% per leg on the
52-stock universe: return decays smoothly with no cliffs (22.13%/yr at
0% down to 7.95%/yr at a stress 0.5%/leg), walk-forward stays consistent
and all 4 quarters stay positive at every level tested. The 1,500-seed
significance test barely moves either (p≈0.015 at `top_k=5` whether
slippage is 0%, 0.10%, or 0.50%) — a mathematical consequence of applying
the same slippage to both the real strategy and the random control, not
new evidence of robustness to the specific risk the Fifty-first entry
raised (that "just cratered" stocks may face worse-than-average real
slippage, which this symmetric test can't detect). One of two flagged
gaps closed; the multiple-comparisons-correction ask remains open. See
CLAUDE.md's "Fifty-second" section for the full breakdown.

**Closing the multiple-comparisons gap (Fifty-third entry,
`multiple_comparisons.py`)** — applied Bonferroni and Benjamini-Hochberg
corrections to every p-value this project's `--significance` random-control
check has ever produced (7 total: IBS rotation's 3 portfolio sizes, RSI-2
rotation's 3, the IBS+low-vol composite's 1), all re-derived fresh in one
sitting for a consistent snapshot. At the honest family of everything
actually tried with this methodology (m=7), **zero of the seven p-values
survive either correction** — even IBS rotation's own best case misses the
Bonferroni-corrected threshold by roughly 2x. At m=25 (the Fifty-first
entry's own already-documented count of internal parameter/universe
variants tried within the IBS-rotation line itself), IBS rotation's best
p-value **also fails**, by roughly 7x — the only reading under which it
survives is an artificially narrow m=3 (just its own 3 portfolio sizes)
that undercounts this project's real search. Both of the Fifty-first
entry's concrete asks are now closed (slippage, multiple comparisons); the
project's repeated "p<0.05, corroborated" framing since the Thirty-ninth
entry should be read as retired, not requalified. See CLAUDE.md's
"Fifty-third" section for the full breakdown.

**Closing the whole-share-rounding gap (Fifty-fourth entry,
`probe_ibs_rotation.py` / `_widen.py`)** — added a `whole_shares` option
(default `False`, old behavior unchanged) that rounds each pick's notional
DOWN to a whole number of shares at its fill price instead of assuming
continuous notional, per the Thirty-ninth entry's own flagged gap. At the
low end of this project's target range (₹30,000 capital, ₹6,000/position),
real rounding costs about 1 percentage point of annual return
(20.95%/yr -> 19.96%/yr) purely from leftover un-invested cash each
month — walk-forward stays consistent and all 4 quarters stay positive,
including Q4. At ₹100,000 the effect is negligible (22.19%/yr ->
21.97%/yr). One thing the original flag worried about turned out not to
bite in this actual 10-year backtest: zero months, at either capital
level, ever had to skip a pick outright for being unaffordable — the
concern was real in principle but the specific expensive names it named
(`MARUTI.NS` etc.) simply never got picked as a bottom-5 IBS name during
this window. See CLAUDE.md's "Fifty-fourth" section for the full
breakdown.

## Other mechanisms explored as standalone probe scripts (not ported into the architecture)

`probe_gap_fill.py` (intraday gap-fill mean reversion — bet that an
opening gap reverts toward yesterday's close within the session) failed
the same way the sector sweep and calendar-effects mechanisms did: a
narrow band of the parameter space looks marginally positive on `^NSEI`,
but it sign-flips on walk-forward, is mixed across quarters, and doesn't
reproduce on `^NSEBANK` or `RELIANCE.NS` — see CLAUDE.md's "Tenth" entry
for the full numbers.

`probe_ibs.py` (Internal Bar Strength mean reversion — long when today's
close fell near today's own low, `IBS = (close-low)/(high-low) < 0.2`) found
exactly one passing instrument (`GC=F`, gold) out of 8 tested — a 12.5% hit
rate at or below the sector sweep's already-distrusted chance-level 18%. The
one survivor's internals look real (smooth perturbation, no quarter decay,
and sizing up to 1% risk more than triples the return) but one instrument
out of eight is thin ground — see CLAUDE.md's "Thirteenth" entry.

`probe_iron_condor_real_data.py` (weekly Nifty iron condor, real NSE
option-chain prices instead of Black-Scholes synthesis — see below and
CLAUDE.md's "Fourteenth" entry) is the strongest mechanism found in this
project on raw robustness, but real market pricing shows a much thinner
edge than the earlier synthetic backtest implied, and it's separately
blocked by a capital-tier problem regardless of pricing method.

`probe_supertrend.py` (SuperTrend — a sticky ATR ratchet-band trend
follower, reproducing a widely-cited open-source implementation) found
4/12 instruments passing walk-forward, a chance-level 33% hit rate; three
of the four fail quarter-split or perturbation (one, `AXISBANK.NS`, only
works at the exact default parameter — a single-point-fit red flag). The
one clean survivor, `CL=F` (oil), clears quarter-split, perturbation, AND
sizing (2% risk reaches 3.78%/year at 7.4% drawdown, genuine scaling, not
dilution) — comparable in quality to this project's best results, but
still just one instrument out of a chance-level sweep. **Retested on 8 more
commodities/FX pairs (the instrument class `CL=F` belongs to) — 0/8
passed**, confirming `CL=F` doesn't generalize within its own instrument
class and was the lucky draw its chance-level screen already implied might
exist. Closed; see CLAUDE.md's "Seventeenth"/"Eighteenth" entries.

`probe_gold_silver_ratio.py` (gold/silver ratio mean reversion — a real,
widely-followed commodity pairs trade, distinct from the equity pairs
trading already tried) found **0/18 parameter configs** passing
walk-forward, and unlike every prior cost-driven failure in this project
(silver scalping, overnight-drift), the gross P&L itself is near zero
across the whole grid — no real edge being masked by costs, just a flat
result. See CLAUDE.md's "Nineteenth" entry.

`probe_trend_volume_ibs.py` — this project's first ORIGINAL strategy, not
sourced from a forum thread or open-source repo: a hypothesis built from
three prior findings (IBS's lone `GC=F` survivor, RSI-2's real trend
filter, CMF-volume's own `GC=F` survivor), testing whether requiring all
three to agree on entry extends IBS's narrow edge. **0/10 instruments
passed** — and strikingly, `GC=F` itself (the one instrument where both
ingredient strategies individually worked) fails when combined, at every
one of 10 perturbed configs. Stacking independently-real signals didn't
compound their edges; it destroyed them — the same direction (if a
different mechanism) as the Eleventh entry's exit-side stacking finding.
See CLAUDE.md's "Twenty-second" entry.

`probe_low_volatility.py` (Low-Volatility Anomaly / "Betting Against
Beta" — a real academic factor: rank a 20-stock NSE universe by trailing
volatility, hold the least-volatile basket, rebalance monthly) passes
walk-forward cleanly (both halves positive, 8/8 perturbation configs
smooth, no cliffs) — but quarter-split shows the **exact same recent-
quarter decay** already seen with Donchian/BTC-USD and momentum rotation:
Q1-Q3 strongly positive, **Q4 (2024-2026) negative in every tested
config**, while the broad universe benchmark was actually +6.3% over the
same window — genuine underperformance, not just a falling market. Third
independent mechanism to hit this exact pattern in this exact recent
window, which is itself the more interesting finding than any one
instance. See CLAUDE.md's "Twenty-fourth" entry.

`probe_pead.py` (Post-Earnings Announcement Drift — a real academic
anomaly, event-driven rather than a continuous technical signal: buy a
stock that beat earnings estimates and hold for weeks, short a miss)
initially passed a 12-config walk-forward grid at every tested value on a
20-stock universe — the strongest aggregate screen since options-selling
— but per-symbol breakdown showed it was concentrated in 3 stocks
(`TATASTEEL.NS`/`SUNPHARMA.NS`/`ONGC.NS`, 15% hit rate). **Retested on a
doubled 40-stock universe (the standard next step for any promising-
looking result here) and it didn't replicate**: the same config flips
INCONSISTENT across walk-forward halves, and the full grid drops from
12/12 passing to 4/12. The "winning" stocks reshuffle too — new names
become the top contributors while the original top contributor
(`TATASTEEL.NS`) shrinks to near breakeven. Closed, the same way
SuperTrend's oil survivor was closed by its own 8-instrument retest. A
real capital-deployment bug was also caught and fixed along the way (the
first version allowed 130% of capital "deployed" simultaneously during
earnings-season clustering; rewritten to properly skip over-allocating
signals) — a permanent improvement kept regardless of PEAD's own fate.
See CLAUDE.md's "Thirtieth"/"Thirty-first" entries.

`probe_portfolio_combo.py` — a genuinely different test: not a new
signal, and not stacking signals into one trade (already found to hurt
twice), but the standard portfolio-construction question this project had
never asked — does combining several of its own already-validated
real-but-thin survivors (RSI-2, both `threebar` survivors, MACD, Turtle
Soup, each on its own instrument) produce a better combined return than
any one alone? **No** — the 5-way combination returns 0.30%/year at 4.1%
drawdown, worse on both dimensions than just running `turtlesoup`/
`AXISBANK.NS` alone (1.71%/year at 1.1% drawdown). Re-tested with only
the 3 strongest components (dropping RSI-2/MACD) — still worse than the
single best one alone. These edges are correlated (all long-biased NSE
equity strategies) and individually too thin for diversification to
produce a free lunch. See CLAUDE.md's "Thirty-second" entry.

Tested whether MARKET STRUCTURE, not mechanism, explains this project's
ceiling — reused `turtlesoup` and `threebar` unmodified against a genuine
small/mid-cap universe (12 stocks never used elsewhere here:
`DEEPAKNTR.NS`, `CROMPTON.NS`, `RADICO.NS`, `APLAPOLLO.NS`,
`JUBLPHARMA.NS`, `SYMPHONY.NS`, `VGUARD.NS`, `RATNAMANI.NS`,
`PERSISTENT.NS`, `GRAPHITE.NS`, `ELGIEQUIP.NS`, `KAJARIACER.NS`), on the
hypothesis that less-arbitraged stocks might leave more room for a
technical edge. **No improvement** — Turtle Soup reproduces its exact
25% hit rate unchanged; 3-bar breakout gets WORSE (16.7% vs its original
50%, and 5/12 instruments hit the drawdown breaker outright — higher
small-cap volatility hurt this strategy's risk control more than it
helped). One clean individual survivor (`SYMPHONY.NS`, remarkably stable
quarter-split) emerged but without the sizing response this project's
best large-cap results have — thin, ~1.18%/year at the only safe
setting. See CLAUDE.md's "Thirty-third" entry.

`regime.py` (market-regime classifier: trend-vs-200-day-SMA crossed with a
realized-volatility percentile rank, no lookahead) — the first entry to
condition on market regime rather than test a new signal or instrument.
Current `^NSEI` regime as of 2026-09-17: `down_low_vol`. Tested two
hypotheses against this project's own 5 best-known survivors: (a) gating
mean-reversion strategies to low-vol regimes and continuation strategies to
high-vol regimes — no consistent improvement, and the one case that looked
like a win (`threebar`/`SBIN.NS`'s drawdown dropping from 10.5% to 0.2%)
turned out to only survive at the exact default regime threshold, the same
single-point-fit red flag already seen with Squeeze/SuperTrend; (b)
regime-based strategy SWITCHING (Turtle Soup gated to choppy regimes,
Donchian to trending, on `AXISBANK.NS`) — nets ~flat, clearly worse than
running Turtle Soup alone. See CLAUDE.md's "Thirty-fourth" entry.

### Options premium selling (weekly Nifty iron condor) — real edge, wrong capital tier, and now real-data validated

Not a daily-bar or intraday-candle strategy, so it doesn't fit either
family above — a defined-risk short strangle (iron condor) sold weekly on
Nifty, harvesting the volatility risk premium. First tested with
Black-Scholes-synthesized premiums (CLAUDE.md's "Seventh" entry): +20.5%
CAGR, no quarter-decay, the most robust-looking result in the whole
project — but flagged immediately as unvalidated against real prices, and
separately shown to need ~₹350,000-475,000 of capital to size one lot
safely (Nifty's SEBI-mandated lot size makes one spread's max loss 20-30%
of a ₹30,000 account — see "Ninth").

Later validated against **real NSE F&O bhavcopy data** (free — `jugaad-data`
for older dates, NSE's own new-format UDIFF bhavcopy fetched directly for
newer dates, both covering 2015-2026, no paid Kite "Historical" subscription
needed). 140 real weekly cycles (2024-2026): the edge's *direction* holds up
(77% win rate, both walk-forward halves positive) but the *magnitude* was
drastically overstated by the synthetic pricing — real annualized return
at the capital this strategy needs is only ~2-3%/year, not 20%+, likely
below a risk-free rate. See CLAUDE.md's "Fourteenth" entry for the full
numbers. Net effect: this mechanism is now closed out on two independent
grounds (capital access AND thin real magnitude), not just one.

## Two variants

`--variant reversal` (default) is the rule described below. `--variant
breakout` is its trend-following inverse (trade WITH a breakout instead of
fading it) — built to test whether a trending instrument suits it better.
Tested on oil: it didn't — 0% win rate over 21 trades, 3x worse than the
reversal variant. See `CLAUDE.md` for the detailed comparison.

## The rule

1. Track the highest and lowest H1 (hourly) bar **open** price seen so far
   today — two lines.
2. **Short**: once price trades *above* the highest-open line, arm a short.
   The first time price falls back *down* through that line, enter.
3. **Long**: once price trades *below* the lowest-open line, arm a long. The
   first time price rises back *up* through that line, enter.
4. Initial stop-loss = the day's high-so-far (short) / low-so-far (long).
5. Move to breakeven+1 at +5 units profit; trail to entry+5 at +10 units
   profit (tune these — the source's 5/10 are forex pips, meaningless for
   an index or a ₹500 stock).
6. Entries fire on every price update, not on bar close — the source is
   explicit that waiting for the bar to close defeats the method.
7. Optional filter: also require the current M15 bar's open to be beyond
   the line before entering (a later addition to the source thread, for
   traders who found themselves entering too early).

See the docstring at the top of `strategy.py` for the exact interpretation
decisions made turning that prose into code (line-fires-once-per-level,
generalised profit units, etc.) — check them against what you actually
intend.

## Layout

```
strategy.py       Pure state machine: HighLowOpenStrategy + TrailingStopManager.
                  No network code — this is what's unit tested.
risk.py           Position sizing (% risk per trade) + a daily-loss circuit breaker.
data.py           Thin Kite historical_data wrapper.
data_yfinance.py  Free historical data via Yahoo Finance — no Kite subscription needed.
kite_client.py    Auth (login_url / generate_access_token) + real order placement.
paper_broker.py   Simulated fills + P&L tracking. The default broker everywhere.
backtest.py       CLI: run the strategy over historical data (yfinance, free, or Kite).
run_live.py       CLI: poll Kite in real time, paper-trade by default.
tests/            pytest, network-free — runs against plain floats, no fixtures needed.
```

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**To backtest for free, no account needed:** you're done — skip straight to
"Backtest (free, no Kite subscription)" below.

**To go further (live quotes, real order placement)** you need a **Kite
Connect** developer app. Zerodha offers two tiers when you create one at
https://developers.kite.trade/apps:

- **Personal** (free) — order placement only. **No historical data, no live
  quotes/WebSockets** — `data.py`/`run_live.py` need the paid tier to work.
- **Connect** (~₹500/30 days, billed via your Zerodha account) — adds
  historical data + live quotes. Needed for `backtest.py --source kite` and
  for `run_live.py` at all.

Also note: as of April 1, 2026, SEBI requires **live orders to come from a
registered static IP** — register one on your Kite Connect profile page
before ever using `--live`.

Put the app's `api_key`/`api_secret` in `.env` (`cp .env.example .env`
first). Kite access tokens expire daily (~7:30am IST); each trading day:

```
python -c "from kite_client import login_url; print(login_url())"
# open the URL, log in, copy request_token from the redirect URL
python -c "from kite_client import generate_access_token; print(generate_access_token('PASTE_REQUEST_TOKEN'))"
# paste the printed access_token into .env as KITE_ACCESS_TOKEN
```

## Run

```
pytest -v   # strategy/risk/broker logic — no network, safe anytime
```

### Backtest (free, no Kite subscription)

```
python backtest.py --source yfinance --symbol RELIANCE.NS --period 30d
python backtest.py --source yfinance --symbol '^NSEI' --period 60d --m15-filter
```

`--symbol` is a Yahoo Finance ticker (`.NS` suffix for NSE stocks, `^NSEI`
for the Nifty 50 index). `--period` is how far back to pull (Yahoo caps
intraday history: up to ~60 days for the 15m/5m data this uses). Yahoo's
NSE data is best-effort, not exchange-of-record — fine for validating
strategy logic, not a substitute for real broker data before trading real
money.

**Tune the trailing-stop thresholds to your instrument** — the source
thread's +5/+10 defaults are forex pips and will misfire wildly on
something like the Nifty index (confirmed: 146 trades / -5,447 P&L over 60
days with the untuned defaults, dropping to 77 trades with wider
thresholds below):

```
python backtest.py --source yfinance --symbol '^NSEI' --period 60d \
  --breakeven-trigger 30 --breakeven-offset 5 --trail-trigger 60 --trail-offset 30
```

**Check for overfitting before trusting any single result** — add
`--walk-forward` to split the sample into two independent halves and report
both separately:

```
python backtest.py --source yfinance --symbol INFY.NS --period 60d --walk-forward
```

A real edge should look broadly similar on both halves. In practice, every
"profitable" single-period result found so far (INFY.NS, HDFCBANK.NS,
ITC.NS) turned out **inconsistent** across the two halves once checked this
way — profitable on one half, losing on the other — which is the standard
signature of noise, not a real edge. Don't trust a whole-period number that
hasn't been walk-forward checked.

### Backtest against real Kite data (needs the Connect tier)

```
python backtest.py --source kite --token 256265 --from 2026-08-01 --to 2026-09-01
```

`--token` is a Kite `instrument_token` — look one up via
`kite.instruments("NSE")` / `kite.instruments("NFO")`, or Kite's published
instrument dump.

### Live/paper runner (needs the Connect tier)

```
python run_live.py --token 256265 --symbol NIFTY --exchange NSE              # paper mode, 15s polling (default)
python run_live.py --token 256265 --symbol NIFTY --exchange NSE --stream     # paper mode, real-time WebSocket price
```

`--stream` uses Kite's own WebSocket (`KiteTicker`, in `kite_ticker.py`)
instead of polling for price — matters because entries fire on every price
update, not bar close, so streaming catches the exact first touch of a line
instead of lagging by up to one poll interval. H1/M15 candles still refresh
periodically either way (they don't change every second). Written against
KiteTicker's documented API but, like the rest of this repo's Kite
integration, untested against a live connection — the polling path is the
better-exercised default.

## Going live

Real orders require **both**:

```
export MONEYMAKING_LIVE=true
python run_live.py --token <token> --symbol <symbol> --exchange <NFO|NSE> --live
```

Don't flip this on until you've backtested and paper-traded to your own
satisfaction. `risk.py`'s `max_daily_loss_pct` circuit breaker is there as a
backstop, not a substitute for validating the strategy first.

## Transaction costs

`PaperBroker(commission_per_trade=...)` / `backtest.py --commission-per-trade`
deducts a flat cost per round-trip trade (default 0). **Always set this to a
realistic estimate before trusting a backtest result** — a systematic search
found a config that looked like a genuine edge (silver, 60d, +22,310 gross
across 278 trades) that flipped net-negative once a conservative $4/trade
cost was applied. A zero-cost backtest overstates high-trade-count results
the most.

## Risk management

Two independent, layered circuit breakers in `risk.py` (checked before
every new entry in both `backtest.py` and `run_live.py`):

- **Daily loss limit** (`max_daily_loss_pct`, default 2%) — resets every day.
- **Cumulative drawdown limit** (`max_drawdown_pct`, default 10%) —
  tracks equity from its all-time peak and **never resets automatically**.
  This one was added after a 60-day silver backtest lost 61% of starting
  capital: nothing was tracking cumulative equity, only same-day P&L, so
  the backtest kept "trading" long past the point a real account would
  have been wiped out. `reset_capital()` clears it, but only call that
  deliberately (a fresh backtest segment, or a real decision to
  re-fund/restart) — never automatically from a runner.

Also available: `RiskManager.volatility_position_size(atr)` sizes off an
instrument's own recent volatility (via `indicators.average_true_range`)
instead of the strategy's own stop distance — useful as a sanity cap
(`min()` of the two) when the strategy's stop is tighter than the
instrument actually moves, which is common on a fast-moving instrument.
Not wired into `backtest.py`/`run_live.py` by default — the strategy's own
day-high/low stop is still the primary sizing input; ATR sizing is there to
cross-check it.

## Known limitations

- `backtest.py` approximates each minute's intra-bar path as
  open → high → low → close, since Kite's historical API gives OHLC, not
  ticks. This can misorder which of a minute's high/low actually came
  first — a real limitation, not a bug to silently trust past.
- `run_live.py --stream` (see above) addresses the polling lag using
  KiteTicker, but is itself untested against a live connection.
- Daily access-token regeneration is manual (see Setup) — Zerodha's login
  is deliberately behind 2FA, and this repo doesn't try to script around it.
- **Every commodity/FX backtest in this project (all `probe_*.py` scripts
  and `backtest_daily.py`) sizes positions as `risk_amount / stop_distance`
  with no connection to the instrument's real exchange lot size** —
  correct for equity shares (buyable one at a time), silently wrong for
  currency/commodity derivatives (fixed-lot contracts only). Found via a
  striking-looking IBS-on-FX result that fell apart once checked: one real
  USDINR lot needs ~₹299,000 of capital to size safely within a 0.5%
  risk-per-trade standard, the same capital-tier wall that closed out this
  project's options-selling line. **Follow-up check on the actual gold/oil
  survivors** (applying a real USD/INR conversion, not this project's
  existing $-as-₹ simplification for GC=F/CL=F): oil is blocked at every
  real MCX contract size (₹328k-1.3M needed even at the smallest Mini
  contract). Gold initially looked like it might have an escape hatch via
  a small "Gold Petal" (1g) contract (₹14,638-58,550 needed) — **checked
  directly against Kite's real, live, unauthenticated instrument master
  (`https://api.kite.trade/instruments`) and confirmed no such contract
  exists on MCX**: MCX genuinely offers exactly `GOLD`/`GOLDM`/`GOLDGUINEA`
  (standard/Mini/8g), nothing smaller. This closes the whole capital-tier
  investigation — every real "survivor" this project found (options
  selling, SuperTrend's oil, IBS's/volume's gold) is now confirmed blocked
  by a genuine capital wall at this project's target scale, with no
  remaining unverified escape hatch. See CLAUDE.md's "Twenty-fifth"
  through "Twenty-ninth" entries for the full arc.
