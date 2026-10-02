import pandas as pd

import probe_insider_trading_signal as it


def test_category_to_type_keeps_only_the_two_named_insider_categories():
    assert it._category_to_type("Promoters") == "promoter"
    assert it._category_to_type("Promoter Group") == "promoter"
    assert it._category_to_type("Director") == "officer"
    assert it._category_to_type("Key Managerial Personnel") == "officer"
    # the ESOP-flood category found in 2016 data (HDFCBANK/HDFC: thousands of routine employee exercises)
    # must NOT be classified as either insider type
    assert it._category_to_type("Employees/Designated Employees") is None
    assert it._category_to_type("Immediate relative") is None
    assert it._category_to_type("Other") is None
    assert it._category_to_type("-") is None
    assert it._category_to_type(None) is None


def test_parse_rows_filters_buy_sell_only_and_strips_time_from_broadcast_date():
    rows = [
        # kept: promoter Buy, genuine open-market purchase
        dict(date="02-May-2026 16:46", symbol="RELTD", personCategory="Director", tdpTransactionType="Buy",
             acqMode="Market Purchase", secVal="7000000"),
        # dropped: Pledge is not an open-market trade
        dict(date="02-May-2026 14:20", symbol="MWL", personCategory="Promoters", tdpTransactionType="Pledge",
             acqMode="Pledge Creation", secVal="682347220"),
        # dropped: Employees/Designated Employees (the ESOP-flood category)
        dict(date="03-May-2026 09:00", symbol="HDFCBANK", personCategory="Employees/Designated Employees",
             tdpTransactionType="Buy", acqMode="ESOP", secVal="50000"),
        # kept: promoter Sell, genuine open-market sale
        dict(date="04-May-2026 10:15", symbol="ABC", personCategory="Promoter Group", tdpTransactionType="Sell",
             acqMode="Market Sale", secVal="1234567"),
    ]
    df = it.parse_rows(rows)
    assert list(df["symbol"]) == ["RELTD", "ABC"]
    assert list(df["buy_sell"]) == ["BUY", "SELL"]
    assert list(df["deal_type"]) == ["officer", "promoter"]
    assert list(df["qty"]) == [7000000.0, 1234567.0]
    assert df["date"].iloc[0] == pd.Timestamp("2026-05-02")  # time-of-day stripped off the broadcast timestamp


def test_parse_rows_drops_non_open_market_acquisition_modes():
    # a "Buy" transaction type that isn't acqMode=="Market Purchase" (Preferential Offer, Gift, ESOP,
    # Inter-se-Transfer, Conversion of security, Bonus) is not a discretionary market decision and must be
    # dropped even though personCategory/tdpTransactionType alone would otherwise keep it - the real confound
    # found live (16% of promoter BUY rows in a sample quarter were one of these, not a genuine market purchase).
    # Symmetric check on the Sell side: only acqMode=="Market Sale" is kept.
    rows = [
        dict(date="01-Jun-2026 10:00", symbol="A", personCategory="Promoters", tdpTransactionType="Buy",
             acqMode="Preferential Offer", secVal="1000"),
        dict(date="01-Jun-2026 10:00", symbol="B", personCategory="Promoters", tdpTransactionType="Buy",
             acqMode="Gift", secVal="1000"),
        dict(date="01-Jun-2026 10:00", symbol="C", personCategory="Promoters", tdpTransactionType="Sell",
             acqMode="Inter-se-Transfer", secVal="1000"),
        dict(date="01-Jun-2026 10:00", symbol="D", personCategory="Promoters", tdpTransactionType="Buy",
             acqMode="Market Purchase", secVal="1000"),
        dict(date="01-Jun-2026 10:00", symbol="E", personCategory="Promoters", tdpTransactionType="Sell",
             acqMode="Market Sale", secVal="1000"),
    ]
    df = it.parse_rows(rows)
    assert list(df["symbol"]) == ["D", "E"]


def test_parse_rows_drops_rows_with_unparseable_value_or_date():
    rows = [
        dict(date="not-a-date", symbol="XYZ", personCategory="Director", tdpTransactionType="Buy",
             acqMode="Market Purchase", secVal="100"),
        dict(date="05-May-2026 10:00", symbol="XYZ", personCategory="Director", tdpTransactionType="Buy",
             acqMode="Market Purchase", secVal="not-a-number"),
    ]
    df = it.parse_rows(rows)
    assert len(df) == 0


def test_build_universe_respects_min_events_and_nets_by_value_via_reused_net_events():
    deals = pd.DataFrame([
        # AAA: 3 promoter BUY-net days (value nets to BUY each day)
        dict(date=pd.Timestamp("2021-01-01"), symbol="AAA", buy_sell="BUY", qty=100, deal_type="promoter"),
        dict(date=pd.Timestamp("2021-02-01"), symbol="AAA", buy_sell="BUY", qty=200, deal_type="promoter"),
        dict(date=pd.Timestamp("2021-02-01"), symbol="AAA", buy_sell="SELL", qty=50, deal_type="promoter"),
        dict(date=pd.Timestamp("2021-03-01"), symbol="AAA", buy_sell="BUY", qty=300, deal_type="promoter"),
        # BBB: only 1 promoter BUY day -> excluded at min_events=2
        dict(date=pd.Timestamp("2021-01-01"), symbol="BBB", buy_sell="BUY", qty=500, deal_type="promoter"),
        # an officer-type row for AAA must not leak into the promoter universe
        dict(date=pd.Timestamp("2021-04-01"), symbol="AAA", buy_sell="BUY", qty=400, deal_type="officer"),
    ])
    syms, events_by_symbol = it.build_universe(deals, "promoter", "BUY", min_events=2)
    assert syms == ["AAA"]
    assert len(events_by_symbol["AAA"]) == 3
    assert pd.Timestamp("2021-04-01") not in events_by_symbol["AAA"]
