import io

import numpy as np
import pandas as pd

import probe_bulk_deals_signal as b


def test_parse_csv_strips_indian_comma_grouping_from_quantity():
    # "9,08,279" is lakh/crore grouping, not thousands - a naive to_numeric would NaN it out (the real bug this
    # project's own first smoke-test run caught: 22 parsed rows against the unparsed file's true 6,236).
    csv = (
        "Date ,Symbol ,Security Name ,Client Name ,Buy / Sell ,Quantity Traded ,Trade Price / Wght. Avg. Price ,Remarks \n"
        '01-JAN-2016,AUTOLITIND,Autolite (India) Ltd,HITEISH ARORA,BUY,"9,08,279",72.79,-\n'
        '02-JAN-2016,GMBREW,GM Breweries,SOME CLIENT,SELL,"1,234",50.0,-\n'
    )
    df = b.parse_csv(csv.encode("utf-8-sig"), "bulk_deals")
    assert list(df["qty"]) == [908279.0, 1234.0]
    assert list(df["symbol"]) == ["AUTOLITIND", "GMBREW"]
    assert list(df["buy_sell"]) == ["BUY", "SELL"]
    assert df["date"].iloc[0] == pd.Timestamp("2016-01-01")
    assert (df["deal_type"] == "bulk_deals").all()


def test_net_events_nets_same_day_buy_and_sell_and_drops_ties():
    df = pd.DataFrame([
        # AAA: net BUY (100 buy, 40 sell)
        dict(date=pd.Timestamp("2020-01-01"), symbol="AAA", buy_sell="BUY", qty=100, deal_type="bulk_deals"),
        dict(date=pd.Timestamp("2020-01-01"), symbol="AAA", buy_sell="SELL", qty=40, deal_type="bulk_deals"),
        # BBB: net SELL
        dict(date=pd.Timestamp("2020-01-01"), symbol="BBB", buy_sell="SELL", qty=90, deal_type="bulk_deals"),
        dict(date=pd.Timestamp("2020-01-01"), symbol="BBB", buy_sell="BUY", qty=10, deal_type="bulk_deals"),
        # CCC: exact tie -> excluded
        dict(date=pd.Timestamp("2020-01-01"), symbol="CCC", buy_sell="BUY", qty=50, deal_type="bulk_deals"),
        dict(date=pd.Timestamp("2020-01-01"), symbol="CCC", buy_sell="SELL", qty=50, deal_type="bulk_deals"),
        # block deal on the same day/symbol must not leak into the bulk_deals side
        dict(date=pd.Timestamp("2020-01-01"), symbol="AAA", buy_sell="SELL", qty=1000, deal_type="block_deals"),
    ])
    side = b.net_events(df, "bulk_deals")
    assert side.loc[(pd.Timestamp("2020-01-01"), "AAA")] == "BUY"
    assert side.loc[(pd.Timestamp("2020-01-01"), "BBB")] == "SELL"
    assert (pd.Timestamp("2020-01-01"), "CCC") not in side.index
    side_block = b.net_events(df, "block_deals")
    assert side_block.loc[(pd.Timestamp("2020-01-01"), "AAA")] == "SELL"


def _close_series(n=40, seed=0, jump_at=None, jump=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n)
    rets = rng.normal(0, 0.01, n)
    if jump_at is not None:
        rets[jump_at] = jump
    px = 100 * np.cumprod(1 + rets)
    return pd.Series(px, index=[d.date() for d in idx])


def test_fwd_return_is_a_lag1_fill_with_no_lookahead():
    close = _close_series(n=20)
    d = close.index[5]
    # entry at close[d+1], exit at close[d+1+h] - never close[d] itself (the event day's own close is not used)
    h = 3
    expected = close.iloc[5 + 1 + h] / close.iloc[5 + 1] - 1
    assert abs(b.fwd_return(close, d, h) - expected) < 1e-12
    # an event on the last available date can't have a forward return at all
    assert b.fwd_return(close, close.index[-1], 1) is None
    # a date not in the index at all (e.g. a real holiday) is also None, not a crash
    import datetime as dt
    assert b.fwd_return(close, dt.date(1999, 1, 1), 1) is None


def test_eligible_dates_matches_fwd_return_validity_exactly():
    close = _close_series(n=30)
    close.iloc[10] = np.nan  # one bad bar
    h = 5
    elig = b.eligible_dates(close, h)
    for d in close.index:
        computable = b.fwd_return(close, d, h) is not None
        assert (d in elig) == computable, d


def test_event_test_detects_a_planted_effect_and_not_a_null_one():
    rng = np.random.default_rng(1)
    closes = {}
    events = {}
    h = 5
    # 30 stocks; each gets 10 "BUY" events. Half the stocks get a planted positive jump the day after each event
    # (i.e. exactly where fwd_return looks); the other half have pure noise.
    for i in range(30):
        n = 250
        close = _close_series(n=n, seed=100 + i)
        elig = b.eligible_dates(close, h)
        ev_dates = list(rng.choice(elig[:-30], size=10, replace=False))
        if i < 15:
            idx = {d: pos for pos, d in enumerate(close.index)}
            for d in ev_dates:
                close.iloc[idx[d] + 1 + h] *= 1.03  # bump the EXIT bar (pos+1+h) so fwd_return's p1/p0 rises
        closes[f"S{i}"] = close
        events[f"S{i}"] = ev_dates
    planted = {k: v for k, v in events.items() if int(k[1:]) < 15}
    nullset = {k: v for k, v in events.items() if int(k[1:]) >= 15}
    res_planted = b.event_test(closes, planted, h, np.random.default_rng(2), draws=800)
    res_null = b.event_test(closes, nullset, h, np.random.default_rng(3), draws=800)
    assert res_planted["actual"] > 0.01
    assert res_planted["p"] < 0.05
    assert res_null["p"] > 0.05


def test_apply_rule_mirrors_sell_signals_short_for_the_cost_check():
    res = pd.DataFrame([
        dict(deal_type="bulk_deals", side="BUY", h=5, actual=0.01, p=0.01, halves=(0.008, 0.012), n_events=100, n_stocks=10),
        dict(deal_type="bulk_deals", side="SELL", h=5, actual=-0.01, p=0.01, halves=(-0.008, -0.012), n_events=100, n_stocks=10),
        dict(deal_type="bulk_deals", side="SELL", h=5, actual=0.01, p=0.01, halves=(0.008, 0.012), n_events=100, n_stocks=10),
    ])
    out = b.apply_rule(res, cost=0.0025)
    assert out.loc[0, "advance"]  # BUY, positive return above cost
    assert out.loc[1, "advance"]  # SELL with a negative raw return -> shorting it clears cost
    assert not out.loc[2, "advance"]  # SELL with a POSITIVE raw return -> shorting it loses money, must not advance
