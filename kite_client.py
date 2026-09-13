"""
Zerodha Kite Connect client wrapper: auth + order placement.

Auth flow (Kite Connect requires this manually, daily — it's not something
this repo automates, since Zerodha's login is behind 2FA on purpose):
  1. `python -c "from kite_client import login_url; print(login_url())"`,
     open the URL, log in, get redirected to your app's redirect URL with
     `?request_token=...` in the query string.
  2. `python -c "from kite_client import generate_access_token; print(generate_access_token('PASTE_REQUEST_TOKEN'))"`
  3. Put the printed access_token in .env as KITE_ACCESS_TOKEN. It expires
     around 7:30am IST the next day — repeat daily before trading.
"""
import config  # noqa: F401  (loads .env as a side effect)
import os
from kiteconnect import KiteConnect


def get_kite_client() -> KiteConnect:
    api_key = os.environ["KITE_API_KEY"]
    kite = KiteConnect(api_key=api_key)
    access_token = os.environ.get("KITE_ACCESS_TOKEN")
    if access_token:
        kite.set_access_token(access_token)
    return kite


def login_url() -> str:
    return get_kite_client().login_url()


def generate_access_token(request_token: str) -> str:
    api_key = os.environ["KITE_API_KEY"]
    api_secret = os.environ["KITE_API_SECRET"]
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    return data["access_token"]


def place_order(kite: KiteConnect, *, tradingsymbol: str, exchange: str, transaction_type: str,
                 quantity: int, product: str, order_type: str = "MARKET", price: float | None = None) -> str:
    """Places a REAL order on your Zerodha account. Only ever called from
    run_live.py when --live is passed AND MONEYMAKING_LIVE=true is set."""
    return kite.place_order(
        variety=kite.VARIETY_REGULAR,
        exchange=exchange,
        tradingsymbol=tradingsymbol,
        transaction_type=transaction_type,
        quantity=quantity,
        product=product,
        order_type=order_type,
        price=price,
    )
