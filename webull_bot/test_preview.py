"""Quick sanity test — preview a SPX (SPXW) bull put spread via Webull API.

Run:
    cd /path/to/StocksTradingTest
    WEBULL_APP_KEY=... WEBULL_APP_SECRET=... python -m webull_bot.test_preview [YYYY-MM-DD]
"""
import sys
import time

sys.path.insert(0, ".")

from webull_bot.client import build_trade_client
from webull_bot.execution import ExecutionEngine
from webull_bot.market_data import find_best_spread, get_spx_price, get_vix_price

ACCOUNT_ID = "QJJGQCBGAN8M2JL2C1OA1J37KB"

print("=== Webull Bot — Preview Test ===")

spx = get_spx_price()
vix = get_vix_price()
print(f"SPX: {spx:.2f}  VIX: {vix:.2f}")

expiry = sys.argv[1] if len(sys.argv) > 1 else "2026-05-12"
print(f"Expiry: {expiry}")

spread = find_best_spread(
    spx_price=spx,
    otm_pct=0.01,
    spread_width=50,
    min_credit=1.50,
    yf_options_symbol="^SPX",
    expiry=expiry,
)
if not spread:
    print("No qualifying spread found")
    sys.exit(1)

print(f"Selected: {int(spread.short_strike)}/{int(spread.long_strike)}P  credit_mid={spread.mid:.2f}")

trade_client = build_trade_client()
execution = ExecutionEngine(trade_client, ACCOUNT_ID)

# Try SPXW first (weekly SPX designation), fallback to XSP if that also fails
for sym in ["SPXW", "XSP"]:
    if sym == "XSP":
        # XSP is 1/10 scale — scale strikes down
        s_strike = round(spread.short_strike / 10)
        l_strike = round(spread.long_strike / 10)
        width = 5
        limit = round(spread.mid / 10, 2)
    else:
        s_strike = spread.short_strike
        l_strike = spread.long_strike
        limit = round(spread.mid - 0.05, 2)

    print(f"\nTrying symbol={sym}  {int(s_strike)}/{int(l_strike)}P  limit={limit:.2f}")
    time.sleep(1.5)

    try:
        result = execution.preview_spread(
            symbol=sym,
            expiry=expiry,
            short_strike=s_strike,
            long_strike=l_strike,
            quantity=1,
            limit_price=limit,
        )
        print(f"Status: {result['status_code']}")
        print(f"Body: {result['body'][:300]}")
        if result['status_code'] == 200:
            print(f"\n✓ {sym} works — use symbol: \"{sym}\" in config.yaml")
            break
    except Exception as e:
        err = str(e)
        print(f"ERROR: {err[:200]}")
        if sym == "XSP":
            print("\nBoth SPX and XSP failed — check account permissions")
