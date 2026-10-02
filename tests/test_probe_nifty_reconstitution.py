import pandas as pd

import probe_nifty_reconstitution as nr


def test_events_data_integrity():
    # every row parses to a real date, and ADD != DELETE within the same row (a symbol can't be both)
    for date, add, delete in nr.EVENTS:
        ts = pd.Timestamp(date)
        assert ts.year >= 2005
        assert add != delete
        assert add.isupper() and delete.isupper()
    # no duplicate (date, symbol, side) triple - would silently double-count one event
    add_seen, del_seen = set(), set()
    for date, add, delete in nr.EVENTS:
        assert (date, add) not in add_seen, f"duplicate ADD event: {date} {add}"
        assert (date, delete) not in del_seen, f"duplicate DELETE event: {date} {delete}"
        add_seen.add((date, add))
        del_seen.add((date, delete))


def test_events_by_symbol_splits_add_and_delete_and_keeps_repeats():
    add = nr.events_by_symbol("ADD")
    delete = nr.events_by_symbol("DELETE")
    # the 2011-03-25 GRASIM/SUZLON event was excluded for having no citation (see EVENTS comment) -
    # confirm it never made it into either dict
    assert pd.Timestamp("2011-03-25") not in add.get("GRASIM", [])
    assert pd.Timestamp("2011-03-25") not in delete.get("SUZLON", [])
    assert pd.Timestamp("2017-05-26") in add.get("VEDL", [])
    assert pd.Timestamp("2017-05-26") in delete.get("GRASIM", [])
    assert pd.Timestamp("2018-04-02") in add.get("GRASIM", [])
    # a symbol that only ever appears on the ADD side must not leak into the DELETE dict
    assert "TCS" not in delete
    assert pd.Timestamp("2005-02-25") in add["TCS"]


def test_apply_rule_mirrors_add_long_delete_short_for_the_cost_check():
    res = pd.DataFrame([
        dict(side="ADD", h=5, actual=0.01, p=0.01, halves=(0.008, 0.012), n_events=50, n_stocks=50),
        dict(side="DELETE", h=5, actual=-0.01, p=0.01, halves=(-0.008, -0.012), n_events=50, n_stocks=50),
        dict(side="DELETE", h=5, actual=0.01, p=0.01, halves=(0.008, 0.012), n_events=50, n_stocks=50),
    ])
    out = nr.apply_rule(res, cost=0.0025)
    assert out.loc[0, "advance"]  # ADD, positive raw return above cost -> long clears cost
    assert out.loc[1, "advance"]  # DELETE with a negative raw return -> shorting it clears cost
    assert not out.loc[2, "advance"]  # DELETE with a POSITIVE raw return -> shorting it loses money
