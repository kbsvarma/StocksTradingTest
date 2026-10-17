"""
Read-only selftest. Proves your API key signs correctly and the account is
reachable. Places NO orders. Run this first.

    python kalshi_mm/selftest.py

Expected: exchange status, your cash balance, and any open positions.
"""

from __future__ import annotations

import logging
import sys

from kalshi_client import Credentials, KalshiAPIError, KalshiClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("selftest")


def main() -> int:
    # dry_run=True is irrelevant here (all calls are GET) but kept explicit.
    try:
        creds = Credentials.load()
    except RuntimeError as e:
        log.error("Credential load failed: %s", e)
        return 2

    client = KalshiClient(creds=creds, dry_run=True)

    log.info("1) Exchange status (public, unsigned)")
    try:
        st = client.exchange_status()
        log.info("   %s", st)
    except Exception as e:
        log.error("   FAILED: %s", e)
        return 1

    log.info("2) Account balance (signed — this is the real auth test)")
    try:
        bal = client.get_balance()
        # balance is reported in cents
        cents = bal.get("balance")
        dollars = (cents / 100) if isinstance(cents, (int, float)) else None
        log.info("   raw=%s  (~$%.2f)", bal, dollars if dollars is not None else float("nan"))
    except KalshiAPIError as e:
        log.error("   AUTH FAILED (%s): %s", e.status, e.text[:200])
        log.error("   -> check KALSHI_KEY_ID and that the PEM matches that key id.")
        return 1
    except Exception as e:
        log.error("   FAILED: %s", e)
        return 1

    log.info("3) Open positions")
    try:
        pos = client.get_positions(limit=50)
        market_positions = pos.get("market_positions", []) or []
        if not market_positions:
            log.info("   none")
        for p in market_positions:
            log.info("   %s", p)
    except Exception as e:
        log.error("   FAILED: %s", e)
        return 1

    log.info("\nAll read-only checks passed. Auth works. No orders were placed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
