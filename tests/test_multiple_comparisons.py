from multiple_comparisons import bonferroni, benjamini_hochberg


def test_bonferroni_threshold_and_pass_fail():
    pvalues = [("a", 0.001), ("b", 0.03)]
    threshold, passed = bonferroni(pvalues, alpha=0.05)
    assert threshold == 0.05 / 2
    assert passed == [True, False]


def test_bonferroni_m_override_for_a_larger_search_family():
    """m can be overridden to correct a single p-value against a larger
    search family than the p-values actually listed - the Fifty-third
    entry's m=25 internal-search-family check."""
    threshold, passed = bonferroni([("x", 0.014)], alpha=0.05, m=25)
    assert threshold == 0.05 / 25
    assert passed == [False]


def test_benjamini_hochberg_distinguishes_rank_scaled_from_raw_alpha_bug():
    """The most common BH implementation bug is comparing every p-value
    against raw alpha directly instead of the rank-scaled (k/m)*alpha
    threshold. This input is chosen so the two give DIFFERENT answers:
    correct BH keeps only the 2 smallest p-values; a buggy raw-alpha
    comparison would incorrectly keep 5 (every p <= 0.05)."""
    pvalues = [
        ("p1", 0.001), ("p2", 0.008), ("p3", 0.039), ("p4", 0.041),
        ("p5", 0.042), ("p6", 0.06), ("p7", 0.074), ("p8", 0.205),
        ("p9", 0.212), ("p10", 0.216),
    ]
    result = benjamini_hochberg(pvalues, alpha=0.05)
    assert result == [True, True, False, False, False, False, False, False, False, False]


def test_benjamini_hochberg_all_significant():
    pvalues = [("a", 0.001), ("b", 0.002)]
    assert benjamini_hochberg(pvalues, alpha=0.05) == [True, True]


def test_benjamini_hochberg_none_significant():
    pvalues = [("a", 0.9), ("b", 0.8)]
    assert benjamini_hochberg(pvalues, alpha=0.05) == [False, False]


def test_benjamini_hochberg_unsorted_input_order_preserved_in_output():
    """Input order (not p-value order) must be preserved in the returned
    list, since callers zip it back against the original (label, p) pairs."""
    pvalues = [("high", 0.9), ("low", 0.001)]
    result = benjamini_hochberg(pvalues, alpha=0.05)
    assert result == [False, True]
