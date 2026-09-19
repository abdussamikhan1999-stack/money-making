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

PVALUES below is the actual family: every p-value this project's
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
# council review actually objected to being ignored. All 7 numbers are from
# one fresh rerun (2026-09-18, same session, same data snapshot) rather than
# the individually-recorded numbers scattered across CLAUDE.md entries -
# this project's own Fortieth/Forty-eighth entries already documented that
# yfinance's per-symbol retry behavior can shift results slightly run-to-
# run, so re-deriving all of them together in one sitting is more honest
# than mixing numbers logged in different sessions. See CLAUDE.md's
# "Fifty-third" entry for the raw script output each of these came from.
# CAUTION (Fifty-seventh entry): at m>=29 the Benjamini-Hochberg column flips
# IBS rotation top_k=5/8 to PASS. That is an ARTIFACT, not new evidence: the
# Fifty-seventh entry's rows are near-identical nested configs, several sitting
# on a circular-rotation control's resolution floor (~0.0073), and BH's step-up
# rule lets a cluster of small p-values lift everyone else's critical value.
# Bonferroni is the operative criterion; IBS's retired significance claim
# (Fifty-third entry) is NOT reinstated by this table. (The flip disappears at
# m=39 once the Fifty-eighth entry's honest grid-free rows are added, which
# confirms it was an artifact of the floor-valued rows, not signal.)
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
    # Nifty PCR contrarian long on NIFTYBEES (Fifty-sixth entry), 1,500-seed
    # circular-shift control, 1-day fill lag, 5 of the 12 swept configs.
    ("Nifty PCR entry=0.80 window=26 (Fifty-sixth)", 0.052),
    ("Nifty PCR entry=0.85 window=26 (Fifty-sixth)", 0.051),
    ("Nifty PCR entry=0.90 window=13 (Fifty-sixth)", 0.123),
    ("Nifty PCR entry=0.90 window=26 (Fifty-sixth)", 0.084),
    ("Nifty PCR entry=0.90 window=52 (Fifty-sixth)", 0.069),
    ("Nifty PCR entry=0.95 window=26 (Fifty-sixth)", 0.327),
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
    ("Fear-buy grid-free india-VIX>=1.4 (Fifty-eighth)", 0.1937),
    ("Fear-buy grid-free india-VIX>=1.5 (Fifty-eighth)", 0.0631),
    ("Fear-buy grid-free india-VIX>=1.7 (Fifty-eighth)", 0.0263),
    ("Fear-buy grid-free US-VIX>=1.4 (Fifty-eighth)", 0.2565),
    ("Fear-buy grid-free US-VIX>=1.5 (Fifty-eighth)", 0.3104),
    ("Fear-buy grid-free US-VIX>=1.7 (Fifty-eighth)", 0.4304),
    ("Fear-buy S&P500 1990+ enter+0d (Fifty-eighth)", 0.2220),
    ("Fear-buy S&P500 1990+ enter+5d (Fifty-eighth)", 0.1995),
    ("Fear-buy S&P500 1990+ enter+10d (Fifty-eighth)", 0.1356),
    ("Fear-buy S&P500 1990+ enter+15d (Fifty-eighth)", 0.0602),
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
    ("Trend gate SMA100 top_k=5, drawdown (Sixty-first)", 0.040),
    ("Trend gate SMA150 top_k=5, drawdown (Sixty-first)", 0.062),
    ("Trend gate SMA200 top_k=5, drawdown (Sixty-first)", 0.116),
    ("Trend gate SMA100 top_k=8, drawdown (Sixty-first)", 0.016),
    ("Trend gate SMA150 top_k=8, drawdown (Sixty-first)", 0.100),
    ("Trend gate SMA200 top_k=8, drawdown (Sixty-first)", 0.153),
    # Sixty-second entry: the same gate on the INDEX alone. p(drawdown as low as random off-months);
    # S&P rows sit at the 2,000-draw floor (1/2001).
    ("Index gate NIFTY 2008+ SMA100, drawdown (Sixty-second)", 0.0700),
    ("Index gate NIFTY 2008+ SMA150, drawdown (Sixty-second)", 0.0110),
    ("Index gate NIFTY 2008+ SMA200, drawdown (Sixty-second)", 0.1769),
    ("Index gate S&P500 1950+ SMA100, drawdown (Sixty-second)", 0.0005),
    ("Index gate S&P500 1950+ SMA150, drawdown (Sixty-second)", 0.0005),
    ("Index gate S&P500 1950+ SMA200, drawdown (Sixty-second)", 0.0005),
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
    print("\n--- internal-search family (m=25, per the Fifty-first entry's ---")
    print("--- own count) - Bonferroni only, BH needs the full ranked    ---")
    print("--- list which isn't available at this family size            ---")
    report([("IBS rotation best p across top_k=3/5/8", IBS_ROTATION_BEST_P)],
           args.alpha, m=IBS_INTERNAL_SEARCH_FAMILY_SIZE)
