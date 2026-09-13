from kite_ticker import TickStream


def test_handle_ticks_forwards_only_the_subscribed_instrument():
    seen = []
    stream = TickStream(api_key="x", access_token="y", instrument_token=256265, on_ltp=seen.append)

    stream._handle_ticks(ws=None, ticks=[
        {"instrument_token": 256265, "last_price": 24100.5},
        {"instrument_token": 999999, "last_price": 1.0},  # a different instrument — must be ignored
        {"instrument_token": 256265, "last_price": 24102.0},
    ])

    assert seen == [24100.5, 24102.0]


def test_handle_connect_subscribes_and_sets_ltp_mode():
    calls = []

    class FakeWs:
        MODE_LTP = "ltp"

        def subscribe(self, tokens):
            calls.append(("subscribe", tokens))

        def set_mode(self, mode, tokens):
            calls.append(("set_mode", mode, tokens))

    stream = TickStream(api_key="x", access_token="y", instrument_token=256265, on_ltp=lambda _: None)
    stream._handle_connect(FakeWs(), response=None)

    assert calls == [("subscribe", [256265]), ("set_mode", "ltp", [256265])]
    assert stream._connected.is_set()
