# Research report: money-making repo (2026-09-21)

Research on NSE/Kite strategies, written up for the repo owner. This is research, not financial advice, and nothing here has
been tested with real money.

## Bottom line

No strategy has cleared the bar to trade real money. About 75 mechanisms have been tested (two more, Entries 85-86, are
tested but not yet written up). The only stock-selection candidate left is **IBS rotation**, and it is unproven: a monthly
rotation into oversold stocks that backtests at about 20% a year with a roughly 39% drawdown. Its edge sits almost entirely in
month-end entries, it fails to replicate on different stocks in the recent decade, and it does not survive a
multiple-testing correction.

What holds up is about risk, not extra return:

- Holding equity with gold roughly halves drawdown.
- A trend filter on an index roughly halves drawdown at a cost of 1-3 points a year.
- Nothing tested beats a broad index at retail scale once costs are included.

## What was done this session (Entries 81-86)

| Entry | Question | Result |
|---|---|---|
| **81** Oil-shock regimes, 1947-2026 | What did better after past oil shocks? | Nothing reliable. Energy stocks beat the market in 8 of 12 episodes (+8.5 points over 12 months, p=0.07, not significant after correction) but lagged by 28 points in the first quarter of the current episode. |
| **82** NSE delivery percentage (new data) | Does delivery share predict returns? | Yes, tiny and replicated on two disjoint stock sets (rank IC ~0.02). The top-5 edge is 0.10-0.16% per 5 days against a 0.25% round-trip cost: information without economics. |
| **83** Abnormal trading volume, 20 years | Is there a high-volume premium? | Null. |
| **84** Overnight vs intraday moves, lottery effect | Which part of a price move carries information? | The known 5-day reversal lives in the intraday part, not overnight gaps, and is below cost in the recent period. Overnight persistence and the lottery effect are null. |
| **85** Stock-futures open interest and options put/call | Does buildup or put/call positioning predict? | Preliminary: null under the pre-registered rule. A code review then found seven issues (see below); the corrected rerun is pending. |
| **86** Dividend month premium and dividend yield | Do dividend-based signals work? | Corrected results: the premium appears only in one stock set's earlier decade and is negative in the recent decade; yield is null. Write-up pending. |

Entries 81-84 are committed in PRs #29 and #30 on GitHub.

## Method

- Each signal, direction, horizon and decision rule is written into the code before any score is computed.
- Two disjoint stock sets, and for the volume, price and OI signals, two independent time periods.
- A signal only advances if its gross edge beats the 0.25% round-trip cost.
- The honest multiple-testing family is 742 tests (Bonferroni threshold 0.00007). Only two S&P 500 trend-filter drawdown rows pass, a known effect and a risk overlay, not alpha.
- A positive control (the known 5-day reversal, detected at p~0.0005) shows the nulls are real nulls.
- An independent code review is run before each write-up. Each review so far has found real problems.

## Errors found and corrected

- **Monthly-average leakage (Entry 81).** Bond and gold series are monthly averages, which leak in-month movement into "forward" returns; two apparently significant results were artifacts (bond p 0.01 to 0.08).
- **Volume blackout (Entry 83).** One bad Yahoo day blanked 65 days of the volume signal.
- **Shift null not centred for persistent signals (found in Entry 86's review).** The circular-shift null is not centred at zero for persistent signals such as dividend yield. A Newey-West test that needs no shift null was added and everything re-run; no earlier verdict changed.
- **Entry 85's review (seven findings):**
  - OI2 (buildup confirmed by price direction) inverted the classic reading on the OI-down half.
  - OI is not adjusted for splits and bonuses (54 events).
  - Option and futures OI collapse at monthly expiry, contaminating 5-day change signals.
  - The decision rule was never implemented in code, only applied by eye. It now is (`apply_rule`), and it confirmed that nothing advances in Entries 82-84.
  - The fetch was not crash-safe.
  - The price cache went stale silently.
  - The universe rule used look-ahead.
- **Two of my own claims retracted.** I said delivery percentage "survives removing" reversal and volume; on identical days about 75-85% survives but significance is borderline. I also compared the dividend premium to a US magnitude I could not verify.
- **A slip of mine.** An over-broad edit deleted two functions. The test suite caught it and I restored them from git.

## Most profitable finding so far

The most profitable-looking result is IBS rotation, but it is not a profitable finding because nothing has shown it would make
money out of sample.

| | Return/yr | Max drawdown | Status |
|---|---|---|---|
| IBS rotation (5 most oversold of 52 stocks, monthly) | ~20% (~21% at realistic costs) | ~39% | Unproven |
| Same, with a NIFTY trend filter (top 5, 100-day average) | ~15.8% | ~13.5% | Unproven; one crash in the sample |
| Same, half-hedged with NIFTYBEES | ~12% | ~24% | Older numbers, before the fill-lag fix |
| NIFTY buy-and-hold, same period | ~10.2% | ~38% | Benchmark |
| Random 5 stocks a month, same universe | ~13.5% | not comparable | Control |

Sample: 2016-2026, today's constituents. The realistic edge over random picks is about 6 points a year. The most credible
positive number is a plain 50/50 equity and gold portfolio rebalanced annually: about 14.6% a year on Sensex plus rupee gold
2003-2026 with a 24-27% drawdown, which is a diversification result carried partly by gold's bull run.

## Caveats

- Both stock sets are today's constituents (survivorship), which flatters earlier periods.
- The delivery data covers only about 7 years.
- Costs are modelled, not measured.
- All data is free and unaudited.

## State of the work at the time of writing

Committed and pushed: Entries 81-84 (PRs #29, #30) and this report.

**Uncommitted in the working tree** (on disk in `~/repos/money-making`, branch `entry-85-oi-signals`):

- `probe_oi_signal.py` and its tests: the seven review fixes are applied and unit-tested, but the corrected analysis has NOT been rerun. The `oi_signal_results.csv` in the tree is from the first, pre-fix run and must not be cited.
- `probe_dividend_signal.py` and its tests: corrected results exist in `dividend_signal_results.csv`.
- Shared-framework changes in `probe_delivery_signal.py`: Newey-West test, null-centre diagnostic, `apply_rule`, atomic writes.
- Regenerated result CSVs for Entries 82-84 (they now include the Newey-West p-value and null-centre columns).
- `fo_oi_cache.csv.gz` (F&O bhavcopy data, 2016 to 2026-09-18).

Still to do: rerun the OI analysis with the fixes, register the p-values in `multiple_comparisons.py`, write up Entries 85-86 in CLAUDE.md, README and RESULTS.md, then commit and push together.

**Open item: run both paper trackers on the last trading days of the month (~28-30 Sept).** The only record so far was logged
mid-month, the backtest's worst phase. This is the one test that can add genuinely new information.
