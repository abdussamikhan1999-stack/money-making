import numpy as np

import probe_fear_followup as ff


def test_declustered_events_keeps_first_of_each_cluster_and_respects_gap():
    mask = np.zeros(100, bool)
    mask[[5, 6, 7, 30, 31, 55]] = True
    assert list(ff.declustered_events(mask, gap=21)) == [5, 30, 55]
    assert list(ff.declustered_events(mask, gap=30)) == [5]  # 55 is only 24 days after the last ON day (31)


def test_newey_west_ols_recovers_known_slope_and_flags_it_significant():
    rng = np.random.default_rng(0)
    x = rng.normal(size=2000)
    y = 0.5 * x + rng.normal(size=2000)
    beta, t = ff.newey_west_ols(y, x[:, None], lags=5)
    assert abs(beta[1] - 0.5) < 0.1 and t[1] > 10


def test_random_pool_p_is_small_for_extreme_events_and_large_for_typical_ones():
    pool = np.random.default_rng(1).normal(0.0, 1.0, 5000)
    assert ff.random_pool_p(pool, np.full(5, 3.0), n_draws=20_000) < 0.001
    assert ff.random_pool_p(pool, np.zeros(5), n_draws=20_000) > 0.4


def test_a_long_continuous_spike_yields_one_event_not_one_per_gap():
    mask = np.zeros(200, bool)
    mask[10:100] = True  # a 90-day spike
    assert list(ff.declustered_events(mask, gap=21)) == [10]
