import numpy as np

import probe_famous_strategies2 as f2


def test_sharpe_is_in_excess_of_cash_when_cash_is_given():
    r, c = np.array([0.02, 0.00, 0.02, 0.00]), np.full(4, 0.01)
    assert abs(f2.stats(r, 12, c)["sharpe"]) < 1e-9          # excess = +-0.01 around zero
    assert f2.stats(r, 12)["sharpe"] > 0.5                     # total-return Sharpe of the same series is positive


def test_month_end_rows_drops_the_incomplete_last_month():
    import pandas as pd
    idx = pd.bdate_range("2024-01-01", "2024-03-15")
    rows = f2.month_end_rows(idx)
    assert idx[rows[-1]].month == 2 and len(rows) == 2         # Jan and Feb month-ends; March is incomplete
