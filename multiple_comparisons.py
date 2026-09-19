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
# (Fifty-third entry) is NOT reinstated by this table.
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
