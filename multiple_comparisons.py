"""
Applies a multiple-comparisons correction to this project's own
--significance random-control p-values.

Direct response to the Fifty-first entry's council review: every reported
p-value (Thirty-ninth, Forty-second, Fiftieth, Fifty-second entries) has been
a single-config number, never checked against how many hypotheses were
actually tested before it was reported. Both Bonferroni (family-wise, strict)
and Benjamini-Hochberg (false-discovery-rate, the standard, less punishing
choice for an exploratory multi-config sweep like this project's) are
reported side by side - neither alone is "the" right answer for this kind of
after-the-fact research audit.

ALL_SIGNIFICANCE_TESTS_PVALUES below is the registered family: every p-value this project's
--significance check has produced, from one fresh rerun (2026-09-18, same
session, same data snapshot) rather than the individually-recorded numbers
scattered across CLAUDE.md entries - this project's own Fortieth/Forty-eighth
entries already documented that yfinance's per-symbol retry behavior can
shift results slightly run-to-run, so re-deriving all of them together in
one sitting is more honest than mixing numbers from different sessions.
"""
import argparse


def bonferroni(pvalues, alpha=0.05, m=None):
    """Corrected significance threshold: alpha / m. A p-value survives if
    it's at or below this threshold. m defaults to len(pvalues) but can be
    overridden to correct a single reported p-value against a LARGER search
    family than the p-values actually listed here - needed for an internal
    parameter sweep whose other members were never individually assigned a
    p-value, only a config choice. Bonferroni only needs the family size and
    the one p being tested, unlike Benjamini-Hochberg below, which needs the
    full ranked list and so can't be computed at a family size larger than
    the p-values actually on hand."""
    if m is None:
        m = len(pvalues)
    threshold = alpha / m
    return threshold, [p <= threshold for _, p in pvalues]


def benjamini_hochberg(pvalues, alpha=0.05):
    """Largest k such that the k-th smallest p-value <= (k/m)*alpha; every
    p-value at or below that rank is declared a discovery (controls false
    discovery rate, not family-wise error - less conservative than
    Bonferroni by design)."""
    m = len(pvalues)
    ranked = sorted(range(m), key=lambda i: pvalues[i][1])
    largest_k = 0
    for rank, i in enumerate(ranked, start=1):
        if pvalues[i][1] <= (rank / m) * alpha:
            largest_k = rank
    survivors = set(ranked[:largest_k])
    return [i in survivors for i in range(m)]


def report(pvalues, alpha=0.05, m=None):
    """m=None: family size is len(pvalues), both Bonferroni and BH computed
    normally. m=<explicit int larger than len(pvalues)>: correcting a single
    p-value (or a handful) against a bigger family than is actually listed -
    BH is skipped and printed as "n/a", since it needs the full ranked list
    of all m p-values, not just the ones on hand."""
    bonf_threshold, bonf_pass = bonferroni(pvalues, alpha, m)
    bh_computable = m is None or m == len(pvalues)
    bh_pass = benjamini_hochberg(pvalues, alpha) if bh_computable else [None] * len(pvalues)
    print(f"family size m={m if m is not None else len(pvalues)}, alpha={alpha}")
    print(f"Bonferroni-corrected threshold: {bonf_threshold:.5f} "
          f"(vs. uncorrected {alpha})")
    print(f"{'label':55s} {'raw p':>8s} {'bonferroni':>11s} {'BH (FDR)':>9s}")
    for (label, p), bp, hp in zip(pvalues, bonf_pass, bh_pass):
        bh_str = "n/a" if hp is None else ("PASS" if hp else "fail")
        print(f"{label:55s} {p:8.4f} {'PASS' if bp else 'fail':>11s} "
              f"{bh_str:>9s}")
    return bonf_pass, bh_pass


# The narrow family: exactly the 3 portfolio sizes probe_ibs_rotation_
# significance.py's own docstring says it exists to check ("the edge isn't
# itself a lucky parameter pick") - the Thirty-ninth entry's own stated
# scope, nothing broader.
IBS_ROTATION_PVALUES = [
    ("IBS rotation top_k=3 (Thirty-ninth)", 0.0367),
    ("IBS rotation top_k=5 (Thirty-ninth)", 0.0160),
    ("IBS rotation top_k=8 (Thirty-ninth)", 0.0140),
]

# The broad family: every p-value this project's --significance random-
# control check has EVER produced, across every candidate that methodology
# was applied to while searching for a strategy to report - not just the
# one that was eventually kept. This is the family the Fifty-first entry's
# council review actually objected to being ignored. The first 7 numbers are from
# one fresh rerun (2026-09-18, same session, same data snapshot) rather than
# the individually-recorded numbers scattered across CLAUDE.md entries -
# this project's own Fortieth/Forty-eighth entries already documented that
# yfinance's per-symbol retry behavior can shift results slightly run-to-
# run, so re-deriving all of them together in one sitting is more honest
# than mixing numbers logged in different sessions. See CLAUDE.md's
# "Fifty-third" entry for the raw script output each of these came from.
# CAUTION on the Benjamini-Hochberg column (updated Seventy-eighth entry): this family contains many
# near-identical nested configs (hold-policy rows 5d/8d/10d x top_k, threshold grids) and several rows sitting on
# a control's resolution floor (S&P index gate 0.0005; the rotation floor ~0.0073). BH assumes independent or
# positively dependent tests and lets a cluster of small p-values lift the critical value for everyone, so its PASS
# marks here are informational only: at the current size it passes rows the project has rejected on other grounds
# (e.g. Reversal(21) top_k=8, killed by survivorship stress in the Fifty-ninth entry). Bonferroni against the
# HONEST family size (registered rows + UNREGISTERED_SCAN_CELLS, printed below) is the operative criterion, and
# IBS rotation's retired significance claim (Fifty-third entry) is NOT reinstated by any BH PASS.
# Cells that were run and reported but never registered row-by-row: the Sixty-fourth entry's 84 phase/anchor scan
# cells (21 fixed-step phases + 21 calendar anchors, x IBS(5) and rev(21)), plus the Eighty-first entry's 288-cell
# industry scan, 12 monthly-average bond/gold robustness cells, and 30 Entry 82-84 robustness cells.
UNREGISTERED_SCAN_CELLS = 84 + 288 + 12 + 30 + 64 + 6  # + Eighty-first: industry scan (288) + leaky bond/gold rows (12); + 82-84: spanning (8), reversal positive control (16), IBS-vs-I5 (6); + Eighty-fifth: OI1E/OI2E/PC2E/OI2X unregistered robustness variants (64); + Eighty-seventh: survivorship-stress rerun of the same 6 skew cells

ALL_SIGNIFICANCE_TESTS_PVALUES = IBS_ROTATION_PVALUES + [
    ("RSI-2 rotation top_k=3 (Forty-second)", 0.1987),
    ("RSI-2 rotation top_k=5 (Forty-second)", 0.2840),
    ("RSI-2 rotation top_k=8 (Forty-second)", 0.0240),
    ("IBS+low-vol composite vol_w=0.5,top_k=5 (Fiftieth)", 0.3633),
    # Amihud illiquidity rotation (Fifty-fourth entry), 1,500-seed rerun,
    # same universe/methodology, lookback=21 days.
    ("Amihud illiquidity rotation top_k=3 (Fifty-fourth)", 0.5900),
    ("Amihud illiquidity rotation top_k=5 (Fifty-fourth)", 0.1047),
    ("Amihud illiquidity rotation top_k=8 (Fifty-fourth)", 0.0367),
    # Nifty PCR contrarian long on NIFTYBEES (Fifty-sixth entry): ALL 12 swept configs (4 entry thresholds x 3
    # windows), 1,500-seed circular-shift control, (count+1)/(n+1) p-values as re-derived in the Seventy-eighth
    # entry (the original entry registered only 6 and its comment miscounted them). The 0.85/52 cell is 0.050 at
    # 1,500 seeds, 0.053 at 5,000 and 0.049 at 20,000, i.e. ON the 0.05 line.
    ("Nifty PCR entry=0.80 window=13 (Fifty-sixth)", 0.070),
    ("Nifty PCR entry=0.80 window=26 (Fifty-sixth)", 0.053),
    ("Nifty PCR entry=0.80 window=52 (Fifty-sixth)", 0.133),
    ("Nifty PCR entry=0.85 window=13 (Fifty-sixth)", 0.124),
    ("Nifty PCR entry=0.85 window=26 (Fifty-sixth)", 0.051),
    ("Nifty PCR entry=0.85 window=52 (Fifty-sixth)", 0.049),
    ("Nifty PCR entry=0.90 window=13 (Fifty-sixth)", 0.124),
    ("Nifty PCR entry=0.90 window=26 (Fifty-sixth)", 0.085),
    ("Nifty PCR entry=0.90 window=52 (Fifty-sixth)", 0.070),
    ("Nifty PCR entry=0.95 window=13 (Fifty-sixth)", 0.118),
    ("Nifty PCR entry=0.95 window=26 (Fifty-sixth)", 0.328),
    ("Nifty PCR entry=0.95 window=52 (Fifty-sixth)", 0.220),
    # Macro-regime probe (Fifty-seventh entry), circular-rotation control, phase 0.
    # A = analog forecaster k=8/15/30; B = oil+rupee stress filter (4 cells);
    # C = VIX-spike fear-buy. C is registered at the ROTATION control's
    # resolution floor (~1/137 = 0.0073 for 160 monthly decisions), the
    # conservative reading; its random-subset p (5e-5 for India VIX) is in the entry.
    ("Macro analog k=8 (Fifty-seventh)", 0.060),
    ("Macro analog k=15 (Fifty-seventh)", 0.007),
    ("Macro analog k=30 (Fifty-seventh)", 0.009),
    ("Macro stress brent>=20%,inr>=1% (Fifty-seventh)", 0.188),
    ("Macro stress brent>=20%,inr>=3% (Fifty-seventh)", 0.339),
    ("Macro stress brent>=30%,inr>=1% (Fifty-seventh)", 0.058),
    ("Macro stress brent>=30%,inr>=3% (Fifty-seventh)", 0.339),
    ("Fear-buy india-VIX>=1.4 (Fifty-seventh, rotation floor)", 0.0073),
    ("Fear-buy india-VIX>=1.5 (Fifty-seventh, rotation floor)", 0.0073),
    ("Fear-buy india-VIX>=1.7 (Fifty-seventh, rotation floor)", 0.0073),
    ("Fear-buy US-VIX>=1.4 (Fifty-seventh, rotation floor)", 0.0073),
    ("Fear-buy US-VIX>=1.5 (Fifty-seventh, rotation floor)", 0.0073),
    ("Fear-buy US-VIX>=1.7 (Fifty-seventh, rotation floor)", 0.0073),
    # Fifty-eighth entry: grid-free re-test of the same trigger (one entry per
    # 21d spike cluster, random-day null) - the honest replacement for the six
    # rotation-floor rows above - and the S&P 500 1990+ replication.
    ("Fear-buy grid-free india-VIX>=1.4 (Fifty-eighth, corrected Seventy-seventh)", 0.7947),
    ("Fear-buy grid-free india-VIX>=1.5 (Fifty-eighth, corrected Seventy-seventh)", 0.4986),
    ("Fear-buy grid-free india-VIX>=1.7 (Fifty-eighth, corrected Seventy-seventh)", 0.0921),
    ("Fear-buy grid-free US-VIX>=1.4 (Fifty-eighth, corrected Seventy-seventh)", 0.8076),
    ("Fear-buy grid-free US-VIX>=1.5 (Fifty-eighth, corrected Seventy-seventh)", 0.8160),
    ("Fear-buy grid-free US-VIX>=1.7 (Fifty-eighth, corrected Seventy-seventh)", 0.9197),
    ("Fear-buy S&P500 1990+ enter+0d (Fifty-eighth, corrected Seventy-seventh)", 0.4486),
    ("Fear-buy S&P500 1990+ enter+5d (Fifty-eighth, corrected Seventy-seventh)", 0.7804),
    ("Fear-buy S&P500 1990+ enter+10d (Fifty-eighth, corrected Seventy-seventh)", 0.5931),
    ("Fear-buy S&P500 1990+ enter+15d (Fifty-eighth, corrected Seventy-seventh)", 0.3497),
    # Fifty-ninth entry: fill-lag-1 (real-account-executable) IBS rotation, and
    # the pre-registered 3x3 short-term-reversal grid, 1,500-seed lag-matched
    # random-portfolio control, plus 4 post-hoc extension cells past the grid edge.
    ("IBS rotation top_k=3 lag-1 fill (Fifty-ninth)", 0.0546),
    ("IBS rotation top_k=5 lag-1 fill (Fifty-ninth)", 0.0506),
    ("IBS rotation top_k=8 lag-1 fill (Fifty-ninth)", 0.0300),
    ("Reversal(5) top_k=3 (Fifty-ninth)", 0.3471),
    ("Reversal(5) top_k=5 (Fifty-ninth)", 0.1746),
    ("Reversal(5) top_k=8 (Fifty-ninth)", 0.3977),
    ("Reversal(10) top_k=3 (Fifty-ninth)", 0.0706),
    ("Reversal(10) top_k=5 (Fifty-ninth)", 0.2385),
    ("Reversal(10) top_k=8 (Fifty-ninth)", 0.0560),
    ("Reversal(21) top_k=3 (Fifty-ninth)", 0.3258),
    ("Reversal(21) top_k=5 (Fifty-ninth)", 0.0660),
    ("Reversal(21) top_k=8 (Fifty-ninth)", 0.0027),
    ("Reversal(21) top_k=12 (Fifty-ninth, post hoc)", 0.0160),
    ("Reversal(42) top_k=8 (Fifty-ninth, post hoc)", 0.0526),
    ("Reversal(63) top_k=8 (Fifty-ninth, post hoc)", 0.0899),
    ("Reversal(42) top_k=12 (Fifty-ninth, post hoc)", 0.0227),
    # Sixtieth entry: each pick pays its own Corwin-Schultz half spread (asymmetric-
    # slippage test); IBS/rev(21) top_k=5 at x1 and x2 of the (upward-biased) estimate.
    ("IBS rotation top_k=5, own half-spread x1 (Sixtieth)", 0.0580),
    ("IBS rotation top_k=5, own half-spread x2 (Sixtieth)", 0.0653),
    ("Reversal(21) top_k=5, own half-spread x1 (Sixtieth)", 0.0793),
    ("Reversal(21) top_k=5, own half-spread x2 (Sixtieth)", 0.1006),
    # Sixty-first entry: NIFTY SMA trend gate on IBS rotation. Registered claim = the
    # DRAWDOWN reduction vs random off-months (its return p-values are 0.28-0.71).
    ("Trend gate SMA100 top_k=5, drawdown (Sixty-first, corrected Seventy-sixth)", 0.027),
    ("Trend gate SMA150 top_k=5, drawdown (Sixty-first, corrected Seventy-sixth)", 0.031),
    ("Trend gate SMA200 top_k=5, drawdown (Sixty-first, corrected Seventy-sixth)", 0.077),
    ("Trend gate SMA100 top_k=8, drawdown (Sixty-first, corrected Seventy-sixth)", 0.013),
    ("Trend gate SMA150 top_k=8, drawdown (Sixty-first, corrected Seventy-sixth)", 0.054),
    ("Trend gate SMA200 top_k=8, drawdown (Sixty-first, corrected Seventy-sixth)", 0.078),
    # Sixty-second entry: the same gate on the INDEX alone. p(drawdown as low as random off-months);
    # S&P rows sit at the 2,000-draw floor (1/2001).
    ("Index gate NIFTY 2008+ SMA100, drawdown (Sixty-second, 20k draws)", 0.0550),
    ("Index gate NIFTY 2008+ SMA150, drawdown (Sixty-second, 20k draws)", 0.0072),
    ("Index gate NIFTY 2008+ SMA200, drawdown (Sixty-second, 20k draws)", 0.1492),
    ("Index gate S&P500 1950+ SMA100, drawdown (Sixty-second, 20k draws)", 0.0001),
    ("Index gate S&P500 1950+ SMA150, drawdown (Sixty-second, 20k draws; <=5e-5)", 0.00005),
    ("Index gate S&P500 1950+ SMA200, drawdown (Sixty-second, 20k draws; <=5e-5)", 0.00005),
    # Seventy-sixth: the RETURN p-values of the index gate once the random control pays the same switching cost.
    ("Index gate NIFTY 2008+ SMA100, return (Seventy-sixth, 20k draws)", 0.128),
    ("Index gate NIFTY 2008+ SMA150, return (Seventy-sixth, 20k draws)", 0.172),
    ("Index gate NIFTY 2008+ SMA200, return (Seventy-sixth, 20k draws)", 0.394),
    ("Index gate S&P500 1950+ SMA100, return (Seventy-sixth, 20k draws)", 0.269),
    ("Index gate S&P500 1950+ SMA150, return (Seventy-sixth, 20k draws)", 0.011),
    ("Index gate S&P500 1950+ SMA200, return (Seventy-sixth, 20k draws)", 0.001),
    # Sixty-third entry: 12-1 momentum and 52-week-high rotations, lag 1. p = P(random >= actual);
    # values near 1 mean the strategy UNDERperforms random portfolios.
    ("12-1 momentum rotation top_k=3 (Sixty-third)", 0.9540),
    ("12-1 momentum rotation top_k=5 (Sixty-third)", 0.8195),
    ("12-1 momentum rotation top_k=8 (Sixty-third)", 0.9374),
    ("52-week-high rotation top_k=3 (Sixty-third)", 0.9900),
    ("52-week-high rotation top_k=5 (Sixty-third)", 0.9727),
    ("52-week-high rotation top_k=8 (Sixty-third)", 0.9973),
    # Sixty-sixth entry: hold-h-days-from-month-end policy (post hoc from the horizon curve), IBS(5), lag 1,
    # random control uses the same hold. Later decade 2016-26 (where found), then the earlier decade (OOS).
    ("IBS month-end hold 5d top_k=5, 2016-26 (Sixty-sixth)", 0.0020),
    ("IBS month-end hold 8d top_k=5, 2016-26 (Sixty-sixth)", 0.0020),
    ("IBS month-end hold 10d top_k=5, 2016-26 (Sixty-sixth)", 0.0013),
    ("IBS month-end hold 13d top_k=5, 2016-26 (Sixty-sixth)", 0.0213),
    ("IBS month-end hold 21d top_k=5, 2016-26 (Sixty-sixth)", 0.0600),
    ("IBS month-end hold 5d top_k=8, 2016-26 (Sixty-sixth)", 0.0033),
    ("IBS month-end hold 8d top_k=8, 2016-26 (Sixty-sixth)", 0.0047),
    ("IBS month-end hold 10d top_k=8, 2016-26 (Sixty-sixth)", 0.0127),
    ("IBS month-end hold 13d top_k=8, 2016-26 (Sixty-sixth)", 0.0326),
    ("IBS month-end hold 21d top_k=8, 2016-26 (Sixty-sixth)", 0.0360),
    ("IBS month-end hold 5d top_k=5, 2007-16 OOS (Sixty-sixth)", 0.0013),
    ("IBS month-end hold 10d top_k=5, 2007-16 OOS (Sixty-sixth)", 0.0793),
    ("IBS month-end hold 21d top_k=5, 2007-16 OOS (Sixty-sixth)", 0.2645),
    # Sixty-eighth entry: the same tests on 54 DIFFERENT NSE names (universe B) and on subsets of the original 52.
    ("Universe B hold 5d top_k=5 (Sixty-eighth)", 0.6356),
    ("Universe B hold 8d top_k=5 (Sixty-eighth)", 0.3611),
    ("Universe B hold 10d top_k=5 (Sixty-eighth)", 0.7009),
    ("Universe B hold 13d top_k=5 (Sixty-eighth)", 0.7328),
    ("Universe B hold 21d top_k=5 (Sixty-eighth)", 0.1999),
    ("Universe B full-month IBS top_k=5 (Sixty-eighth)", 0.1872),
    ("Universe B full-month IBS top_k=8 (Sixty-eighth)", 0.2352),
    ("Original 40 large caps hold 5d (Sixty-eighth)", 0.0160),
    ("Original 40 large caps hold 10d (Sixty-eighth)", 0.3037),
    ("Original 40 large caps hold 21d (Sixty-eighth)", 0.1978),
    ("Original 12 small/mid hold 5d top_k=3 (Sixty-eighth)", 0.1359),
    ("Original 12 small/mid hold 10d top_k=3 (Sixty-eighth)", 0.0759),
    ("Original 12 small/mid hold 21d top_k=3 (Sixty-eighth)", 0.1648),
    ("Universe B hold 5d, 2007-16 (Sixty-ninth)", 0.0093),
    ("Universe B hold 10d, 2007-16 (Sixty-ninth)", 0.0693),
    ("Universe B hold 21d, 2007-16 (Sixty-ninth)", 0.0693),
    # Seventieth entry: ETF timing rules vs rotation control (p on final wealth; static 50/50 has no timing to test).
    ("ETF dual momentum L=6m (Seventieth)", 0.147),
    ("ETF dual momentum L=9m (Seventieth)", 0.319),
    ("ETF dual momentum L=12m (Seventieth)", 0.172),
    ("ETF trend gate SMA100 (Seventieth)", 0.347),
    ("ETF trend gate SMA150 (Seventieth)", 0.300),
    ("ETF trend gate SMA200 (Seventieth)", 0.497),
    # Seventy-first entry: cross-sectional rotation across 4 ETFs vs random-pick control.
    ("ETF 4-asset momentum L=6m top_k=1 (Seventy-first)", 0.046),
    ("ETF 4-asset momentum L=6m top_k=2 (Seventy-first)", 0.213),
    ("ETF 4-asset momentum L=12m top_k=1 (Seventy-first)", 0.500),
    ("ETF 4-asset momentum L=12m top_k=2 (Seventy-first)", 0.164),
    ("ETF 4-asset reversal L=1m top_k=1 (Seventy-first)", 0.852),
    ("ETF 4-asset reversal L=1m top_k=2 (Seventy-first)", 0.666),
    # Seventy-second entry: the Seventieth entry's rules on long-history proxies (p on final wealth vs rotation control).
    ("Proxy NIFTY+INR gold, dual momentum 6m (Seventy-second)", 0.256),
    ("Proxy NIFTY+INR gold, dual momentum 9m (Seventy-second)", 0.314),
    ("Proxy NIFTY+INR gold, dual momentum 12m (Seventy-second)", 0.332),
    ("Proxy NIFTY+INR gold, trend gate SMA100 (Seventy-second)", 0.252),
    ("Proxy NIFTY+INR gold, trend gate SMA150 (Seventy-second)", 0.780),
    ("Proxy NIFTY+INR gold, trend gate SMA200 (Seventy-second)", 0.740),
    ("Proxy S&P+USD gold, dual momentum 6m (Seventy-second)", 0.192),
    ("Proxy S&P+USD gold, dual momentum 9m (Seventy-second)", 0.229),
    ("Proxy S&P+USD gold, dual momentum 12m (Seventy-second)", 0.357),
    ("Proxy S&P+USD gold, trend gate SMA100 (Seventy-second)", 0.630),
    ("Proxy S&P+USD gold, trend gate SMA150 (Seventy-second)", 0.023),
    ("Proxy S&P+USD gold, trend gate SMA200 (Seventy-second)", 0.123),
    # Seventy-fourth entry: month-end hold policies on 52 US large caps (US costs), same-hold random control.
    ("US large caps hold 5d, 2007-16 (Seventy-fourth)", 0.0400),
    ("US large caps hold 10d, 2007-16 (Seventy-fourth)", 0.0559),
    ("US large caps hold 21d, 2007-16 (Seventy-fourth)", 0.6633),
    ("US large caps hold 5d, 2016-26 (Seventy-fourth)", 0.5145),
    ("US large caps hold 10d, 2016-26 (Seventy-fourth)", 0.5205),
    ("US large caps hold 21d, 2016-26 (Seventy-fourth)", 0.9970),
    # Seventy-ninth entry: published approaches of famous funds/traders. TSMOM p(Sharpe) vs an EXACT enumeration of all
    # circular rotations; vol-managed equity likewise; Halloween year-block bootstrap; vol breakout bootstrap p(mean<=0).
    ("TSMOM B long/flat L=126, Sharpe (Seventy-ninth)", 0.015),
    ("TSMOM C long/short L=126, Sharpe (Seventy-ninth)", 0.077),
    ("TSMOM B long/flat L=189, Sharpe (Seventy-ninth)", 0.057),
    ("TSMOM C long/short L=189, Sharpe (Seventy-ninth)", 0.155),
    ("TSMOM B long/flat L=252, Sharpe (Seventy-ninth)", 0.005),
    ("TSMOM C long/short L=252, Sharpe (Seventy-ninth)", 0.206),
    ("Vol-managed S&P 1960+ target 10%, Sharpe (Seventy-ninth)", 0.347),
    ("Vol-managed S&P 1960+ target 15%, Sharpe (Seventy-ninth)", 0.238),
    ("Vol-managed NIFTY 2008+ target 10%, Sharpe (Seventy-ninth)", 0.891),
    ("Vol-managed NIFTY 2008+ target 15%, Sharpe (Seventy-ninth)", 0.984),
    ("Halloween S&P 500 1950+ (Seventy-ninth)", 0.0003),
    ("Halloween NIFTY 2008+ (Seventy-ninth)", 0.7483),
    ("Vol breakout ^NSEI long k=0.4 (Seventy-ninth)", 0.850),
    ("Vol breakout ^NSEI long k=0.6 (Seventy-ninth)", 0.443),
    ("Vol breakout ^NSEI long k=0.8 (Seventy-ninth)", 0.303),
    ("Vol breakout ^NSEI short k=0.4 (Seventy-ninth)", 0.323),
    ("Vol breakout ^NSEI short k=0.6 (Seventy-ninth)", 0.582),
    ("Vol breakout ^NSEI short k=0.8 (Seventy-ninth)", 0.548),
    ("Vol breakout SPY long k=0.4 (Seventy-ninth)", 1.000),
    ("Vol breakout SPY long k=0.6 (Seventy-ninth)", 1.000),
    ("Vol breakout SPY long k=0.8 (Seventy-ninth)", 1.000),
    ("Vol breakout SPY short k=0.4 (Seventy-ninth)", 1.000),
    ("Vol breakout SPY short k=0.6 (Seventy-ninth)", 1.000),
    ("Vol breakout SPY short k=0.8 (Seventy-ninth)", 1.000),
    # Eightieth entry: currency carry (control GROSS of cost on both sides), sector-ETF pairs, SVXY term-structure timing.
    ("Currency carry L3/S3, Sharpe vs random 3v3, gross (Eightieth)", 0.0697),
    ("Currency carry L3/S3, wealth vs random 3v3, gross (Eightieth)", 0.0335),
    ("Sector-ETF pairs (SSD top-5), Sharpe vs random pairs (Eightieth)", 0.6471),
    ("Sector-ETF pairs (SSD top-5), P&L vs random pairs (Eightieth)", 0.6771),
    ("SVXY when VIX/VIX3M<1.0, Sharpe exact rotation (Eightieth)", 0.1044),
    ("SVXY when VIX/VIX3M<0.9, Sharpe exact rotation (Eightieth)", 0.0728),
    # Eighty-first entry: oil-shock regimes 1947-2026, 9 pre-registered series x 3 horizons x 2 regimes (54 tests, permutation null).
    # Regime A = WTI +30%/3m at a 12m high; B = A and GS10 +0.50pp/6m. BOND10 = month-end DGS10, GOLD skips month t+1 (code review).
    # The 288-cell industry scan and the 12 leaky BOND10AVG/GOLDAVG rows are UNREGISTERED scan cells.
    ("Regime A MKT h=3m vs random spaced months (Eighty-first)", 0.7676),
    ("Regime A MKT h=6m vs random spaced months (Eighty-first)", 0.8155),
    ("Regime A MKT h=12m vs random spaced months (Eighty-first)", 0.4550),
    ("Regime A SMB h=3m vs random spaced months (Eighty-first)", 0.8010),
    ("Regime A SMB h=6m vs random spaced months (Eighty-first)", 0.7704),
    ("Regime A SMB h=12m vs random spaced months (Eighty-first)", 0.9479),
    ("Regime A HML h=3m vs random spaced months (Eighty-first)", 0.3826),
    ("Regime A HML h=6m vs random spaced months (Eighty-first)", 0.6330),
    ("Regime A HML h=12m vs random spaced months (Eighty-first)", 0.6509),
    ("Regime A MOM h=3m vs random spaced months (Eighty-first)", 0.7828),
    ("Regime A MOM h=6m vs random spaced months (Eighty-first)", 0.3409),
    ("Regime A MOM h=12m vs random spaced months (Eighty-first)", 0.3623),
    ("Regime A GOLD h=3m vs random spaced months (Eighty-first)", 0.8594),
    ("Regime A GOLD h=6m vs random spaced months (Eighty-first)", 0.2264),
    ("Regime A GOLD h=12m vs random spaced months (Eighty-first)", 0.6620),
    ("Regime A BOND10 h=3m vs random spaced months (Eighty-first)", 0.0789),
    ("Regime A BOND10 h=6m vs random spaced months (Eighty-first)", 0.0524),
    ("Regime A BOND10 h=12m vs random spaced months (Eighty-first)", 0.4957),
    ("Regime A OILREL h=3m vs random spaced months (Eighty-first)", 0.5836),
    ("Regime A OILREL h=6m vs random spaced months (Eighty-first)", 0.1660),
    ("Regime A OILREL h=12m vs random spaced months (Eighty-first)", 0.0734),
    ("Regime A GUNSREL h=3m vs random spaced months (Eighty-first)", 0.7688),
    ("Regime A GUNSREL h=6m vs random spaced months (Eighty-first)", 0.5678),
    ("Regime A GUNSREL h=12m vs random spaced months (Eighty-first)", 0.4299),
    ("Regime A TREND h=3m vs random spaced months (Eighty-first)", 0.9477),
    ("Regime A TREND h=6m vs random spaced months (Eighty-first)", 0.7235),
    ("Regime A TREND h=12m vs random spaced months (Eighty-first)", 0.8647),
    ("Regime B MKT h=3m vs random spaced months (Eighty-first)", 0.8772),
    ("Regime B MKT h=6m vs random spaced months (Eighty-first)", 0.4130),
    ("Regime B MKT h=12m vs random spaced months (Eighty-first)", 0.7383),
    ("Regime B SMB h=3m vs random spaced months (Eighty-first)", 0.2764),
    ("Regime B SMB h=6m vs random spaced months (Eighty-first)", 0.8079),
    ("Regime B SMB h=12m vs random spaced months (Eighty-first)", 0.8151),
    ("Regime B HML h=3m vs random spaced months (Eighty-first)", 0.4447),
    ("Regime B HML h=6m vs random spaced months (Eighty-first)", 0.2453),
    ("Regime B HML h=12m vs random spaced months (Eighty-first)", 0.2502),
    ("Regime B MOM h=3m vs random spaced months (Eighty-first)", 0.1713),
    ("Regime B MOM h=6m vs random spaced months (Eighty-first)", 0.1402),
    ("Regime B MOM h=12m vs random spaced months (Eighty-first)", 0.1051),
    ("Regime B GOLD h=3m vs random spaced months (Eighty-first)", 0.0413),
    ("Regime B GOLD h=6m vs random spaced months (Eighty-first)", 0.9472),
    ("Regime B GOLD h=12m vs random spaced months (Eighty-first)", 0.7344),
    ("Regime B BOND10 h=3m vs random spaced months (Eighty-first)", 0.0784),
    ("Regime B BOND10 h=6m vs random spaced months (Eighty-first)", 0.0899),
    ("Regime B BOND10 h=12m vs random spaced months (Eighty-first)", 0.2034),
    ("Regime B OILREL h=3m vs random spaced months (Eighty-first)", 0.3035),
    ("Regime B OILREL h=6m vs random spaced months (Eighty-first)", 0.4821),
    ("Regime B OILREL h=12m vs random spaced months (Eighty-first)", 0.0919),
    ("Regime B GUNSREL h=3m vs random spaced months (Eighty-first)", 0.6714),
    ("Regime B GUNSREL h=6m vs random spaced months (Eighty-first)", 0.0838),
    ("Regime B GUNSREL h=12m vs random spaced months (Eighty-first)", 0.6168),
    ("Regime B TREND h=3m vs random spaced months (Eighty-first)", 0.4550),
    ("Regime B TREND h=6m vs random spaced months (Eighty-first)", 0.4880),
    ("Regime B TREND h=12m vs random spaced months (Eighty-first)", 0.8500),
    # Eighty-second/-third/-fourth entries: daily cross-sectional rank IC, circular-shift null (4,000 draws; floor 0.00025).
    # Delivery 16 (2 signals x 4 h x 2 universes), Volume 32 (x 2 periods), OHLC 48 (4 signals x 3 h x 2 x 2). Direction
    # pre-registered as high => higher return; p is two-sided, so a NEGATIVE-IC hit is registered like any other.
    ("Delivery D1 h=1d univ A (Eighty-second)", 0.0010),
    ("Delivery D1 h=5d univ A (Eighty-second)", 0.0235),
    ("Delivery D1 h=10d univ A (Eighty-second)", 0.0797),
    ("Delivery D1 h=21d univ A (Eighty-second)", 0.4954),
    ("Delivery D2 h=1d univ A (Eighty-second)", 0.3577),
    ("Delivery D2 h=5d univ A (Eighty-second)", 0.2499),
    ("Delivery D2 h=10d univ A (Eighty-second)", 0.0705),
    ("Delivery D2 h=21d univ A (Eighty-second)", 0.1250),
    ("Delivery D1 h=1d univ B (Eighty-second)", 0.0490),
    ("Delivery D1 h=5d univ B (Eighty-second)", 0.0367),
    ("Delivery D1 h=10d univ B (Eighty-second)", 0.0232),
    ("Delivery D1 h=21d univ B (Eighty-second)", 0.2654),
    ("Delivery D2 h=1d univ B (Eighty-second)", 0.2299),
    ("Delivery D2 h=5d univ B (Eighty-second)", 0.3642),
    ("Delivery D2 h=10d univ B (Eighty-second)", 0.1060),
    ("Delivery D2 h=21d univ B (Eighty-second)", 0.0472),
    ("Volume V1 P1 h=1d univ A (Eighty-third)", 0.8485),
    ("Volume V1 P1 h=5d univ A (Eighty-third)", 0.3629),
    ("Volume V1 P1 h=10d univ A (Eighty-third)", 0.3794),
    ("Volume V1 P1 h=21d univ A (Eighty-third)", 0.8800),
    ("Volume V2 P1 h=1d univ A (Eighty-third)", 0.2372),
    ("Volume V2 P1 h=5d univ A (Eighty-third)", 0.9118),
    ("Volume V2 P1 h=10d univ A (Eighty-third)", 0.4944),
    ("Volume V2 P1 h=21d univ A (Eighty-third)", 0.1662),
    ("Volume V1 P2 h=1d univ A (Eighty-third)", 0.4019),
    ("Volume V1 P2 h=5d univ A (Eighty-third)", 0.3247),
    ("Volume V1 P2 h=10d univ A (Eighty-third)", 0.9278),
    ("Volume V1 P2 h=21d univ A (Eighty-third)", 0.9425),
    ("Volume V2 P2 h=1d univ A (Eighty-third)", 0.0192),
    ("Volume V2 P2 h=5d univ A (Eighty-third)", 0.6451),
    ("Volume V2 P2 h=10d univ A (Eighty-third)", 0.6071),
    ("Volume V2 P2 h=21d univ A (Eighty-third)", 0.2022),
    ("Volume V1 P1 h=1d univ B (Eighty-third)", 0.4461),
    ("Volume V1 P1 h=5d univ B (Eighty-third)", 0.4944),
    ("Volume V1 P1 h=10d univ B (Eighty-third)", 0.1462),
    ("Volume V1 P1 h=21d univ B (Eighty-third)", 0.2969),
    ("Volume V2 P1 h=1d univ B (Eighty-third)", 0.6153),
    ("Volume V2 P1 h=5d univ B (Eighty-third)", 0.3934),
    ("Volume V2 P1 h=10d univ B (Eighty-third)", 0.2247),
    ("Volume V2 P1 h=21d univ B (Eighty-third)", 0.1922),
    ("Volume V1 P2 h=1d univ B (Eighty-third)", 0.0145),
    ("Volume V1 P2 h=5d univ B (Eighty-third)", 0.4246),
    ("Volume V1 P2 h=10d univ B (Eighty-third)", 0.9310),
    ("Volume V1 P2 h=21d univ B (Eighty-third)", 0.6178),
    ("Volume V2 P2 h=1d univ B (Eighty-third)", 0.0012),
    ("Volume V2 P2 h=5d univ B (Eighty-third)", 0.1820),
    ("Volume V2 P2 h=10d univ B (Eighty-third)", 0.6251),
    ("Volume V2 P2 h=21d univ B (Eighty-third)", 0.2084),
    ("OHLC O20 P1 h=1d univ A (Eighty-fourth)", 0.3132),
    ("OHLC O20 P1 h=5d univ A (Eighty-fourth)", 0.3689),
    ("OHLC O20 P1 h=21d univ A (Eighty-fourth)", 0.4199),
    ("OHLC G5 P1 h=1d univ A (Eighty-fourth)", 0.4864),
    ("OHLC G5 P1 h=5d univ A (Eighty-fourth)", 0.6433),
    ("OHLC G5 P1 h=21d univ A (Eighty-fourth)", 0.9025),
    ("OHLC I5 P1 h=1d univ A (Eighty-fourth)", 0.1305),
    ("OHLC I5 P1 h=5d univ A (Eighty-fourth)", 0.0495),
    ("OHLC I5 P1 h=21d univ A (Eighty-fourth)", 0.6161),
    ("OHLC MAX P1 h=1d univ A (Eighty-fourth)", 0.0447),
    ("OHLC MAX P1 h=5d univ A (Eighty-fourth)", 0.7611),
    ("OHLC MAX P1 h=21d univ A (Eighty-fourth)", 0.7676),
    ("OHLC O20 P2 h=1d univ A (Eighty-fourth)", 0.6246),
    ("OHLC O20 P2 h=5d univ A (Eighty-fourth)", 0.7733),
    ("OHLC O20 P2 h=21d univ A (Eighty-fourth)", 0.8975),
    ("OHLC G5 P2 h=1d univ A (Eighty-fourth)", 0.6226),
    ("OHLC G5 P2 h=5d univ A (Eighty-fourth)", 0.4389),
    ("OHLC G5 P2 h=21d univ A (Eighty-fourth)", 0.5794),
    ("OHLC I5 P2 h=1d univ A (Eighty-fourth)", 0.0002),
    ("OHLC I5 P2 h=5d univ A (Eighty-fourth)", 0.0002),
    ("OHLC I5 P2 h=21d univ A (Eighty-fourth)", 0.0047),
    ("OHLC MAX P2 h=1d univ A (Eighty-fourth)", 0.0085),
    ("OHLC MAX P2 h=5d univ A (Eighty-fourth)", 0.6763),
    ("OHLC MAX P2 h=21d univ A (Eighty-fourth)", 0.1017),
    ("OHLC O20 P1 h=1d univ B (Eighty-fourth)", 0.2554),
    ("OHLC O20 P1 h=5d univ B (Eighty-fourth)", 0.7888),
    ("OHLC O20 P1 h=21d univ B (Eighty-fourth)", 0.9668),
    ("OHLC G5 P1 h=1d univ B (Eighty-fourth)", 0.3519),
    ("OHLC G5 P1 h=5d univ B (Eighty-fourth)", 0.3269),
    ("OHLC G5 P1 h=21d univ B (Eighty-fourth)", 0.7431),
    ("OHLC I5 P1 h=1d univ B (Eighty-fourth)", 0.0010),
    ("OHLC I5 P1 h=5d univ B (Eighty-fourth)", 0.0187),
    ("OHLC I5 P1 h=21d univ B (Eighty-fourth)", 0.5929),
    ("OHLC MAX P1 h=1d univ B (Eighty-fourth)", 0.8223),
    ("OHLC MAX P1 h=5d univ B (Eighty-fourth)", 0.5841),
    ("OHLC MAX P1 h=21d univ B (Eighty-fourth)", 0.2782),
    ("OHLC O20 P2 h=1d univ B (Eighty-fourth)", 0.2599),
    ("OHLC O20 P2 h=5d univ B (Eighty-fourth)", 0.7918),
    ("OHLC O20 P2 h=21d univ B (Eighty-fourth)", 0.7558),
    ("OHLC G5 P2 h=1d univ B (Eighty-fourth)", 0.3052),
    ("OHLC G5 P2 h=5d univ B (Eighty-fourth)", 0.0912),
    ("OHLC G5 P2 h=21d univ B (Eighty-fourth)", 0.4816),
    ("OHLC I5 P2 h=1d univ B (Eighty-fourth)", 0.0002),
    ("OHLC I5 P2 h=5d univ B (Eighty-fourth)", 0.0002),
    ("OHLC I5 P2 h=21d univ B (Eighty-fourth)", 0.0020),
    ("OHLC MAX P2 h=1d univ B (Eighty-fourth)", 0.0002),
    ("OHLC MAX P2 h=5d univ B (Eighty-fourth)", 0.2644),
    ("OHLC MAX P2 h=21d univ B (Eighty-fourth)", 0.3874),
    # Eighty-fifth entry: stock-futures OI "buildup" + options put/call signals, daily cross-sectional
    # rank IC, same shift-circular-shift null and framework as Entries 82-84. 4 signals (OI1, OI2, PC1,
    # PC2) x 4 h x 2 universes x 2 periods = 64 tests. Registered p = max(p_shift, p_nw) per the probe's
    # own decision rule (persistent signals need both tests to pass, not just the shift null). Decision
    # rule found nothing advancing (registered or unregistered). 64 further cells (OI1E/OI2E/PC2E/OI2X,
    # unregistered robustness variants) are NOT individually listed here, only counted below.
    ("OI1 P1 h=1d univ A (Eighty-fifth)", 0.4736),
    ("OI1 P1 h=5d univ A (Eighty-fifth)", 0.2172),
    ("OI1 P1 h=10d univ A (Eighty-fifth)", 0.3490),
    ("OI1 P1 h=21d univ A (Eighty-fifth)", 0.0988),
    ("OI2 P1 h=1d univ A (Eighty-fifth)", 0.6154),
    ("OI2 P1 h=5d univ A (Eighty-fifth)", 0.6293),
    ("OI2 P1 h=10d univ A (Eighty-fifth)", 0.4656),
    ("OI2 P1 h=21d univ A (Eighty-fifth)", 0.9715),
    ("PC1 P1 h=1d univ A (Eighty-fifth)", 0.3527),
    ("PC1 P1 h=5d univ A (Eighty-fifth)", 0.1627),
    ("PC1 P1 h=10d univ A (Eighty-fifth)", 0.2534),
    ("PC1 P1 h=21d univ A (Eighty-fifth)", 0.2494),
    ("PC2 P1 h=1d univ A (Eighty-fifth)", 0.7133),
    ("PC2 P1 h=5d univ A (Eighty-fifth)", 0.3717),
    ("PC2 P1 h=10d univ A (Eighty-fifth)", 0.5749),
    ("PC2 P1 h=21d univ A (Eighty-fifth)", 0.0745),
    ("OI1 P2 h=1d univ A (Eighty-fifth)", 0.7506),
    ("OI1 P2 h=5d univ A (Eighty-fifth)", 0.9041),
    ("OI1 P2 h=10d univ A (Eighty-fifth)", 0.9200),
    ("OI1 P2 h=21d univ A (Eighty-fifth)", 0.5556),
    ("OI2 P2 h=1d univ A (Eighty-fifth)", 0.5505),
    ("OI2 P2 h=5d univ A (Eighty-fifth)", 0.4839),
    ("OI2 P2 h=10d univ A (Eighty-fifth)", 0.4569),
    ("OI2 P2 h=21d univ A (Eighty-fifth)", 0.5204),
    ("PC1 P2 h=1d univ A (Eighty-fifth)", 0.5042),
    ("PC1 P2 h=5d univ A (Eighty-fifth)", 0.1500),
    ("PC1 P2 h=10d univ A (Eighty-fifth)", 0.2979),
    ("PC1 P2 h=21d univ A (Eighty-fifth)", 0.2249),
    ("PC2 P2 h=1d univ A (Eighty-fifth)", 0.1687),
    ("PC2 P2 h=5d univ A (Eighty-fifth)", 0.5498),
    ("PC2 P2 h=10d univ A (Eighty-fifth)", 0.8550),
    ("PC2 P2 h=21d univ A (Eighty-fifth)", 0.7158),
    ("OI1 P1 h=1d univ B (Eighty-fifth)", 0.8872),
    ("OI1 P1 h=5d univ B (Eighty-fifth)", 0.3849),
    ("OI1 P1 h=10d univ B (Eighty-fifth)", 0.1730),
    ("OI1 P1 h=21d univ B (Eighty-fifth)", 0.2698),
    ("OI2 P1 h=1d univ B (Eighty-fifth)", 0.3104),
    ("OI2 P1 h=5d univ B (Eighty-fifth)", 0.2347),
    ("OI2 P1 h=10d univ B (Eighty-fifth)", 0.7013),
    ("OI2 P1 h=21d univ B (Eighty-fifth)", 0.2907),
    ("PC1 P1 h=1d univ B (Eighty-fifth)", 0.7574),
    ("PC1 P1 h=5d univ B (Eighty-fifth)", 0.3217),
    ("PC1 P1 h=10d univ B (Eighty-fifth)", 0.0577),
    ("PC1 P1 h=21d univ B (Eighty-fifth)", 0.0525),
    ("PC2 P1 h=1d univ B (Eighty-fifth)", 0.1368),
    ("PC2 P1 h=5d univ B (Eighty-fifth)", 0.0515),
    ("PC2 P1 h=10d univ B (Eighty-fifth)", 0.9185),
    ("PC2 P1 h=21d univ B (Eighty-fifth)", 0.3154),
    ("OI1 P2 h=1d univ B (Eighty-fifth)", 0.3105),
    ("OI1 P2 h=5d univ B (Eighty-fifth)", 0.7214),
    ("OI1 P2 h=10d univ B (Eighty-fifth)", 0.2889),
    ("OI1 P2 h=21d univ B (Eighty-fifth)", 0.9943),
    ("OI2 P2 h=1d univ B (Eighty-fifth)", 0.1360),
    ("OI2 P2 h=5d univ B (Eighty-fifth)", 0.5194),
    ("OI2 P2 h=10d univ B (Eighty-fifth)", 0.5514),
    ("OI2 P2 h=21d univ B (Eighty-fifth)", 0.9283),
    ("PC1 P2 h=1d univ B (Eighty-fifth)", 0.3087),
    ("PC1 P2 h=5d univ B (Eighty-fifth)", 0.5156),
    ("PC1 P2 h=10d univ B (Eighty-fifth)", 0.8465),
    ("PC1 P2 h=21d univ B (Eighty-fifth)", 0.3667),
    ("PC2 P2 h=1d univ B (Eighty-fifth)", 0.1290),
    ("PC2 P2 h=5d univ B (Eighty-fifth)", 0.0969),
    ("PC2 P2 h=10d univ B (Eighty-fifth)", 0.1760),
    ("PC2 P2 h=21d univ B (Eighty-fifth)", 0.3642),
    # Eighty-sixth entry: dividend month premium (DIV1, Hartzmark-Solomon) and trailing dividend
    # yield (DYLD) as monthly cross-sectional signals, same 2-universe/2-period framework. DIV1's
    # shift null is centred, so its registered p is the pre-registered shift p; DYLD's ~0.8
    # twelve-month autocorrelation makes the shift null non-centred (mean -1.9 to -2.4 sd from
    # zero), so its registered p is the Newey-West t-test instead (disclosed in the probe's own
    # docstring before scoring). (DIV1 x 1 + DYLD x 3) x 2 universes x 2 periods = 16 tests.
    ("DIV1 P1 h=1m univ A (Eighty-sixth)", 0.7273),
    ("DYLD P1 h=1m univ A (Eighty-sixth)", 0.6355),
    ("DYLD P1 h=3m univ A (Eighty-sixth)", 0.6321),
    ("DYLD P1 h=12m univ A (Eighty-sixth)", 0.9352),
    ("DIV1 P2 h=1m univ A (Eighty-sixth)", 0.1475),
    ("DYLD P2 h=1m univ A (Eighty-sixth)", 0.2197),
    ("DYLD P2 h=3m univ A (Eighty-sixth)", 0.2560),
    ("DYLD P2 h=12m univ A (Eighty-sixth)", 0.5231),
    ("DIV1 P1 h=1m univ B (Eighty-sixth)", 0.1775),
    ("DYLD P1 h=1m univ B (Eighty-sixth)", 0.6021),
    ("DYLD P1 h=3m univ B (Eighty-sixth)", 0.6278),
    ("DYLD P1 h=12m univ B (Eighty-sixth)", 0.3466),
    ("DIV1 P2 h=1m univ B (Eighty-sixth)", 0.0157),
    ("DYLD P2 h=1m univ B (Eighty-sixth)", 0.9379),
    ("DYLD P2 h=3m univ B (Eighty-sixth)", 0.9006),
    ("DYLD P2 h=12m univ B (Eighty-sixth)", 0.9197),
    # Eighty-seventh entry: realized-skewness monthly cross-sectional rotation (Amaya-Christoffersen-
    # Jacobs-Vasquez 2015: low/negative realized skewness of daily returns predicts HIGHER future
    # returns; ascending sort, no negation needed). 52-stock WIDE_UNIVERSE, lag-1 fill, 1,500-seed
    # random-portfolio control, pre-registered 2 windows x top_k 3/5/8 = 6 tests. Clean null, every
    # cell underperforms its own random-portfolio control (p 0.30-0.97); worse under the Fortieth
    # entry's 4-blowup survivorship stress (not individually registered - a due-diligence rerun of
    # the same 6 cells, not a second pre-registered family).
    ("skew(21) top_k=3 (Eighty-seventh)", 0.6696),
    ("skew(21) top_k=5 (Eighty-seventh)", 0.6376),
    ("skew(21) top_k=8 (Eighty-seventh)", 0.8035),
    ("skew(63) top_k=3 (Eighty-seventh)", 0.3031),
    ("skew(63) top_k=5 (Eighty-seventh)", 0.7035),
    ("skew(63) top_k=8 (Eighty-seventh)", 0.9680),
    # Ninety-second entry: day-of-week effect, 5,000-draw random-same-size-subset control, 2
    # independent long-history markets (NIFTY 20y, S&P 500 since inception). 5 weekdays x 2
    # markets = 10 tests, pre-registered before any return was scored.
    ("NIFTY Mon (Ninety-second)", 0.9348),
    ("NIFTY Tue (Ninety-second)", 0.3163),
    ("NIFTY Wed (Ninety-second)", 0.0394),
    ("NIFTY Thu (Ninety-second)", 0.7756),
    ("NIFTY Fri (Ninety-second)", 0.3581),
    ("S&P500 Mon (Ninety-second)", 0.0252),
    ("S&P500 Tue (Ninety-second)", 0.1748),
    ("S&P500 Wed (Ninety-second)", 0.0008),
    ("S&P500 Thu (Ninety-second)", 0.3005),
    ("S&P500 Fri (Ninety-second)", 0.0816),
    # Entries 94-98 (astrology/aesthetics/holiday): their write-ups say "registered" but the rows were never added to
    # this file (found while registering Entry 99: the file held 424 rows, README/CLAUDE.md said 444). Added now from the
    # numbers printed in CLAUDE.md. Entry 97 (astrology strategy) has no p-value, only a walk-forward hit count.
    ("lunar NIFTY new (Ninety-fourth)", 0.197),
    ("lunar NIFTY full (Ninety-fourth)", 0.956),
    ("lunar NIFTY other (Ninety-fourth)", 0.438),
    ("lunar S&P500 new (Ninety-fourth)", 0.080),
    ("lunar S&P500 full (Ninety-fourth)", 0.495),
    ("lunar S&P500 other (Ninety-fourth)", 0.881),
    ("round-number NIFTY near (Ninety-fifth)", 0.388),
    ("round-number NIFTY far (Ninety-fifth)", 0.631),
    ("round-number S&P500 near (Ninety-fifth)", 0.820),
    ("round-number S&P500 far (Ninety-fifth)", 0.172),
    ("Mercury retrograde NIFTY retro (Ninety-sixth)", 0.698),
    ("Mercury retrograde NIFTY direct (Ninety-sixth)", 0.340),
    ("Mercury retrograde S&P500 retro (Ninety-sixth)", 0.645),
    ("Mercury retrograde S&P500 direct (Ninety-sixth)", 0.360),
    ("pre-holiday NIFTY pre (Ninety-eighth)", 0.0252),
    ("pre-holiday NIFTY post (Ninety-eighth)", 0.0388),
    ("pre-holiday NIFTY other (Ninety-eighth)", 0.988),
    ("pre-holiday S&P500 pre (Ninety-eighth)", 0.0002),
    ("pre-holiday S&P500 post (Ninety-eighth)", 0.417),
    ("pre-holiday S&P500 other (Ninety-eighth)", 0.998),
    # Ninety-ninth entry: cross-sectional return seasonality (Heston-Sadka). 3 signals x 2 universes x 2 periods = 12
    # registered; the two SEAS5 P2 cells have only 40 valid months (< the 60 floor) and produce no p-value, so 10 rows.
    # p = max(p_shift, p_nw). None is under 0.05.
    ("SEAS1 P1 univ A (Ninety-ninth)", 0.5817),
    ("SEAS3 P1 univ A (Ninety-ninth)", 0.9510),
    ("SEAS5 P1 univ A (Ninety-ninth)", 0.4753),
    ("SEAS1 P2 univ A (Ninety-ninth)", 0.3387),
    ("SEAS3 P2 univ A (Ninety-ninth)", 0.8335),
    ("SEAS1 P1 univ B (Ninety-ninth)", 0.8708),
    ("SEAS3 P1 univ B (Ninety-ninth)", 0.1623),
    ("SEAS5 P1 univ B (Ninety-ninth)", 0.0667),
    ("SEAS1 P2 univ B (Ninety-ninth)", 0.0580),
    ("SEAS3 P2 univ B (Ninety-ninth)", 0.4379),
    # NOT individually registered (Sixty-fourth entry): 84 further cells, 21 fixed-step phase offsets
    # and 21 calendar-anchored offsets x {IBS(5) top_k=5, rev(21) top_k=8}. Counting them, an honest
    # Bonferroni family is m>=161 (threshold ~0.0003); the smallest p among them is 0.003.
]

# The Fifty-first entry's OWN internal-search family, not a new number
# invented here: that entry's own text states "~25+ internal parameter/
# universe variants tried within the IBS-rotation line itself" before the
# reported config was settled on (the top_k x lookback grid in the
# Thirty-eighth entry, +6 more widening the Thirty-ninth, +3 more in the
# Fortieth). Only IBS rotation's own best p-value can be checked against
# this family size - the other ~22 variants were never individually
# assigned a p-value, only a config choice, so Bonferroni (which only
# needs the count and the one p being tested) applies but BH (which needs
# every p-value in the family) does not.
IBS_INTERNAL_SEARCH_FAMILY_SIZE = 25
IBS_ROTATION_BEST_P = min(p for _, p in IBS_ROTATION_PVALUES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    # Self-check: a p-value far below alpha/m must survive both corrections;
    # one far above must survive neither, regardless of family size.
    demo = [("far below threshold", 0.0001), ("far above threshold", 0.9)]
    bonf_pass, bh_pass = report(demo, args.alpha)
    assert bonf_pass == [True, False]
    assert bh_pass == [True, False]
    print("\nself-check OK\n")

    print("--- narrow family: the 3 IBS rotation portfolio sizes alone ---")
    report(IBS_ROTATION_PVALUES, args.alpha)
    print("\n--- broad family: every candidate this project ever ran the ---")
    print("--- --significance check against, IBS included             ---")
    report(ALL_SIGNIFICANCE_TESTS_PVALUES, args.alpha)
    honest_m = len(ALL_SIGNIFICANCE_TESTS_PVALUES) + UNREGISTERED_SCAN_CELLS
    thr, passed = bonferroni(ALL_SIGNIFICANCE_TESTS_PVALUES, args.alpha, m=honest_m)
    print(f"\n--- HONEST family: {len(ALL_SIGNIFICANCE_TESTS_PVALUES)} registered + {UNREGISTERED_SCAN_CELLS} unregistered scan cells "
          f"= m={honest_m}; Bonferroni threshold {thr:.5f} ---")
    winners = [(l, p) for (l, p), ok in zip(ALL_SIGNIFICANCE_TESTS_PVALUES, passed) if ok]
    print("rows passing the honest Bonferroni threshold:", winners if winners else "none")
    print("NOTE: the BH column above is informational only for this family (see the CAUTION comment: nested rows,")
    print("resolution-floor rows, dependence); Bonferroni against the honest m is the operative criterion.")
    print("\n--- internal-search family (m=25, per the Fifty-first entry's ---")
    print("--- own count) - Bonferroni only, BH needs the full ranked    ---")
    print("--- list which isn't available at this family size            ---")
    report([("IBS rotation best p across top_k=3/5/8", IBS_ROTATION_BEST_P)],
           args.alpha, m=IBS_INTERNAL_SEARCH_FAMILY_SIZE)
