import os
import time
import uuid
from webullsdkcore.client import ApiClient
from webullsdktrade.api import API

client = ApiClient(
    app_key=os.environ["WEBULL_APP_KEY"],
    app_secret=os.environ["WEBULL_APP_SECRET"],
    region_id="us"
)

trade = API(client)

ACCOUNT_ID = os.environ["WEBULL_ACCOUNT_ID"]

# XSP ~739 today, 1% OTM short put = ~731, 5pt wide spread
# Using 2026-05-12 (Monday) expiry
new_orders = [{
    "client_order_id": uuid.uuid4().hex,
    "combo_type": "NORMAL",
    "option_strategy": "VERTICAL",
    "order_type": "LIMIT",
    "quantity": "1",
    "side": "SELL",
    "time_in_force": "DAY",
    "limit_price": "0.20",
    "instrument_type": "OPTION",
    "market": "US",
    "symbol": "XSP",
    "legs": [
        {
            "side": "SELL",
            "quantity": "1",
            "symbol": "XSP",
            "strike_price": "731",
            "option_expire_date": "2026-05-12",
            "instrument_type": "OPTION",
            "option_type": "PUT",
            "market": "US"
        },
        {
            "side": "BUY",
            "quantity": "1",
            "symbol": "XSP",
            "strike_price": "726",
            "option_expire_date": "2026-05-12",
            "instrument_type": "OPTION",
            "option_type": "PUT",
            "market": "US"
        }
    ]
}]

print("=== Preview Bull Put Spread (XSP 731/726P, 2026-05-12) ===")
time.sleep(3)
try:
    resp = trade.order.preview_option(account_id=ACCOUNT_ID, new_orders=new_orders)
    print(resp.status_code, resp.text[:2000])
except Exception as e:
    print(f"ERROR: {e}")
