# Results report — research entries 56–80 (2026-09-19)

One-page consolidation of the research run. Full numbers, caveats and the reasoning behind every
line live in `CLAUDE.md` (numbered entries) and the significance ledger in
`multiple_comparisons.py`. Nothing here has been validated with real money and none of it is
financial advice.

## Headline

- **72 mechanisms tested. None is declared tradable.**
- Significance ledger: **178 registered p-values + 84 scan cells noted but not registered =
  m 262, honest Bonferroni threshold 0.00019.** The only rows that pass are the three S&P 500
  1950+ trend-gate *drawdown* effects (p <= 1e-4 at 20,000 draws): a well-known risk overlay, not alpha.
- The one stock-selection candidate left (**IBS rotation**) is **unproven**.
- What holds up is about *risk*, not return: equity + gold roughly halves drawdown; a trend gate
  cuts index drawdown; nothing tested beats a broad equity index on Sharpe at retail scale.
- Five independent code reviews found **34 issues** in the analysis scripts (Entries 76–80). Several
  had biased results toward a finding (see "Withdrawn or corrected").

## Verdict by idea

| Idea | Entry | Verdict | What decided it |
|---|---|---|---|
| IBS rotation (buy most-oversold stocks monthly) | 38–41, 59–69, 73–74 | **Unproven** | ~20%/yr backtest at a 39% drawdown; edge sits in the first ~5 days after month-end; absent on fresh stocks in the recent decade |
| Equity + gold allocation | 70, 72, 75 | **Risk observation** | 50/50 max drawdown 24–29% with 2008 included vs ~55% S&P alone; return carried by gold bull runs |
| Trend gate on an index | 61, 62, 76, 78 | **Risk overlay** | S&P 1950+ drawdown p <= 1e-4; halves drawdown; best length differs by market |
| Put-call-ratio contrarian long | 56, 78 | Rejected | All 12 configs lose to random timing (best cell p~0.05, rest 0.05–0.33) |
| Macro / geopolitical analog matching | 57 | Rejected | Its p=0.007 was a decision-grid artifact; geopolitical features alone predicted the wrong way |
| VIX-spike fear-buy | 57, 58, 77 | **Null** | First-crossing entries: 14 events, +0.96% vs +0.91% unconditional, p=0.50; S&P 1990+ 38 events, p=0.59 |
| Short-term reversal factor | 59 | Rejected | 0.85 correlated with IBS; 15.6%/yr, 57% DD, p=0.11 with four real blowups added |
| 12-1 momentum, 52-week-high rotation | 63 | Rejected | Underperform random portfolios in all six cells; latest quarter negative in each |
| NIFTYBEES/GOLDBEES dual momentum, per-asset trend gates, 4-ETF rotation | 70–72 | Rejected | No timing edge over random rotation or over holding the mix |
| CTA time-series momentum (11 ETFs) | 79 | Risk tool | Long/short Sharpe 0.38–0.49 (< 0.54 buy&hold); long/flat = no-signal inverse-vol Sharpe with half the drawdown |
| Risk parity (unlevered) | 79 | Risk tool | 5.64%/yr, DD 15.3%, Sharpe 0.60 vs 60/40 0.68; the funds' edge is leverage |
| Volatility-managed equity | 79 | Risk tool | No Sharpe gain on the S&P; worse on NIFTY |
| Halloween / sell in May | 79 | Local, decaying | S&P 1950+ p=0.0003 (gap +0.73%/mo, halves +0.92/+0.55); absent in NIFTY (p=0.75); misses the honest bar |
| Larry Williams volatility breakout | 79 | Null | SPY -0.10% per trade, p=1.0; NIFTY flat |
| Currency carry (9 FX) | 80 | Thin, crash-prone | +2.06%/yr, Sharpe 0.31, DD 31%, 2008 -27%; gross-vs-gross p(Sharpe)=0.070 |
| Sector-ETF pairs | 80 | Null | -0.09%/yr; random pairs did better (p=0.65) |
| Short-vol (SVXY) term-structure timing | 80 | Null since 2018 | 21.7%/yr full sample is the -1x era; -0.21%/yr (Sharpe -0.03) in the -0.5x era; SPY Sharpe 0.85 beats it |

## The standing candidate: IBS rotation

Long the 5 most-oversold stocks (5-day average internal bar strength) of a 52-stock NSE universe,
monthly, equal weight, ~10 years, lag-1 fills.

| Measure | Result |
|---|---|
| Backtest, realistic next-close fill | 20.05%/yr, max DD 39.5% (was 22.3% with a same-bar fill; an independent from-scratch reimplementation gave 20.30%) |
| Cost calibration (0.125%/leg, ~real NSE delivery) | +~2 points/yr; p-values unchanged |
| Where the edge is | Month-end entries (last 5 trading days: 18.9–24.4%/yr, p<=0.05 each); mid-month entries 4.9–14.6%; 21-anchor mean 15.3% vs ~13.5% random |
| First ~5–10 trading days | Gross excess over the universe +0.45–0.61% per trade (t~2.5–2.75, both decades); flat after |
| Asymmetric slippage | IBS picks pay 1.03x the estimated half-spread of random picks (upward-biased proxy) |
| Month-end concentration, corrected | paired t 1.9 in-sample, 0.5 earlier decade |

**Hold-5-days-then-cash vs same-hold random portfolios, p-value:**

| Stock set | 2007–2016 | 2016–2026 |
|---|---|---|
| Original 52 NSE stocks | 0.0013 | 0.0020 (where it was found) |
| 54 different NSE stocks | 0.0093 | 0.64 |
| 52 US large caps | 0.040 | 0.51 |

Every universe shows something in the earlier decade (the survivorship-inflated one, since a
loser-bounce strategy is flattered most by today's constituents); on fresh stocks in the recent decade
nothing replicates. Net economics of the short hold: ~8–13%/yr on capital deployed a quarter to half the
time. The full-month hold does not replicate on 2007–16 (p=0.26). **Weight this as more likely a
discovery-sample finding than a tradable effect.** The forward paper record is the remaining test.

## Robust observations (risk, not return)

- **Equity + gold.** Max drawdown at month-end closes: S&P alone 54.7%, gold alone 41.7%, 50/50 S&P+gold
  29.1%, 50/50 Sensex+INR gold 27.0% (23.8% with annual rebalancing); 11–13% only on samples starting
  after 2008. Drawdown scales with equity share (30%: 17–26%, 70%: 34–37%). Annual rebalancing was at
  least as good as monthly. More gold looks better only because gold beat equity in-sample.
- **Trend gate (cash when the index is below its SMA).** On IBS rotation at top_k=5: Calmar 0.51 -> 1.17/1.03/0.86
  (SMA100/150/200), not robust at top_k=8 under survivorship stress. On the index alone: S&P 1950+ drawdown
  54.7% -> 23–27%, p <= 1e-4; return also beats equal-cost random cash months at SMA150/200 (p=0.011/0.001);
  NIFTY 2008+ is a drawdown device only.

## What was withdrawn or corrected

| Claim | Was | Now | Cause |
|---|---|---|---|
| Fear-buy return per trade | +7.4%, 12 of 12 months | +0.96%, p=0.50 | Decision-grid luck, then a declustering bug that re-entered mid-spike |
| Equity/gold 50/50 drawdown | 11–13% | 24–29% | Samples started after the 2008 crash |
| IBS rotation return | 22%/yr | ~20%/yr | Backtest filled at the same close it ranked on |
| Month-end effect, in-sample t | 2.8 | 1.9 | Months averaged by position, not calendar month |
| PCR "no cell clears 0.05" | none | one at ~0.05 | Six of twelve sweep cells were unregistered |
| Index gate "doesn't time returns" | p 0.16–0.76 | S&P p=0.001 at SMA200 | Control paid no switching cost |
| Carry significance | p(Sharpe)=0.013 | 0.070 | Random control paid several times the turnover cost |
| 2022 risk-parity S&P window | -7.8% | -19.0% | Month labelled by decision date, one month early |
| Short-vol timing | 21.7%/yr | -0.21%/yr since 2018 | Spliced -1x and -0.5x products |

## Data validation

Yahoo's OHLC for NSE large caps was checked against NSE's **official daily bhavcopy** (30 random dates
2018–2026 x 10 stocks = 300 symbol-days): every row's four price ratios agree to 0.000%; ~22% of rows
differ only by a split/bonus adjustment; the rest match exactly. The IBS inputs are official data, and the
NSE overnight-up / intraday-down asymmetry is real. **NIFTYBEES.NS on Yahoo is corrupt** (rows with
Open=High=Low=Close, mean open-to-close -0.41%/day) and is excluded from any open-dependent test.

## Method notes

- Controls: every candidate is compared against a random or rotated-signal control with the same
  mechanics; controls have to pay the same costs and turnover as the strategy.
- Circular-rotation controls have a resolution floor (~1/number of distinct offsets); exact enumeration or
  a random-subset null is used where p-values are small.
- Independent code review *before* writing conclusions: 34 issues found across five reviews; the two that
  mattered most biased toward positive findings. Unit tests and a from-scratch reimplementation of a
  headline number did not catch them.
- The ledger's Benjamini–Hochberg column is informational only for this family (nested and floor-valued
  rows violate its independence assumption); Bonferroni against the honest m is the criterion.

## Today (data through 2026-09-18)

NIFTY 23,346 is below its SMA100/150/200 (23,952 / 24,051 / 24,494): the trend gate reads **RISK-OFF** on all
three. India VIX 11.4, the lowest of any day since 2008 with Brent up 30%+ over 60 days; Brent futures
$103.9. These are readings, not recommendations. `python probe_macro_analog.py --state-only` refreshes them.

## Open items

1. **Run both paper trackers on the last trading days of the month (~28–30 Sept).** The only record so far
   (2026-09-17) was logged mid-month, the backtest's worst phase. The unhedged tracker now also records
   the 5- and 10-day excess automatically.
2. Do not trade IBS with real money before the forward record says something; a diversified low-cost
   equity/gold allocation rebalanced annually, with ~25% drawdown as the honest expectation, is what the
   evidence supports.
3. Consolidate the 24-PR stack (see below); factor the duplicated rotation-control helpers into one shared
   module before adding more tests.
4. Untested: fundamentals-based quality/value (no long free history), futures-curve carry, real execution
   cost for oversold names at the close, a gold bear longer than 2013–19.

## PR stack (merge in this order; each is stacked on the previous)

#5 (Entry 56) -> #6 (57) -> #7 (58) -> #8 (59) -> #9 (60) -> #10 (61–62) -> #11 (63) -> #12 (64) -> #13 (65)
-> #14 (66) -> #15 (67) -> #16 (68) -> #17 (69) -> #18 (70) -> #19 (71) -> #20 (72) -> #21 (73) -> #22 (74)
-> #23 (75) -> #24 (76) -> #25 (77) -> #26 (78) -> #27 (79) -> #28 (80, including this report).
An automated research run will not start while any PR is open.

## Reproduce

Probe scripts (`probe_*.py`), tests (`pytest`, 219 passing) and the ledger (`python multiple_comparisons.py`)
are in this repo. Key entry points: `probe_reversal_rotation.py` (IBS/reversal/momentum/gate/phase/US/universe
tests, flags `--hold --gate --anchor --oos --us --universe-b --index-gate --spread --cost`),
`probe_etf_rotation.py` (`--proxy nifty|sensex|spx`, `--alloc`, `--multi`), `probe_macro_analog.py`
(`--state-only`, `--robust`, `--episodes`), `probe_fear_followup.py`, `probe_pcr_signal.py`,
`probe_famous_strategies.py` and `probe_famous_strategies2.py` (`--only ...`), `probe_vol_breakout.py`.
