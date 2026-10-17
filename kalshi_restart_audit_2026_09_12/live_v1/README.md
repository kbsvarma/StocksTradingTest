# Kalshi crypto z2 pilot

Deployed September 12, 2026 to `varmak@192.168.3.36` at `/home/varmak/kalshi_revalidation_2026_09_12/live_v1`.

The observer is enabled and running. The live service is installed but disabled, and no activation file exists. No real orders were submitted during implementation or verification.

Dashboard: http://192.168.3.36:8507/ — now shows only the current crypto lane, with its own ledger and P&L. Legacy display and 13 history files are archived on the server under `archive/legacy_2026_09_12/`. Source ledgers remain intact for existing services.

Update: the user activated the live service at 15:36 ET on September 12. It was confirmed active and armed. The following staging and verification notes describe the state before that activation.

## Activate once

Run this from your Mac to enable automatic real-money trading:

```bash
ssh varmak@192.168.3.36 /home/varmak/kalshi_revalidation_2026_09_12/live_v1/activate_live.sh
```

This stops observation, performs a read-only account preflight, writes the activation file, and enables the live service. After activation, the service scans each minute and submits qualifying trades automatically; no per-trade or per-window approval is built into it. Systemd restarts it after failure and starts it after reboot. Failed preflight restores observation.

## Strategy and limits

- BTC, ETH, SOL and XRP 15-minute markets; one contract requested per market and at most one order attempt per market.
- Favorite signal ask at least 85¢ and strictly below 93¢, spread at most 2¢, 3–10 minutes remaining, cushion z at least 2.
- Uses completed Coinbase minute bars, a pre-open Coinbase close as strike proxy, and 60 completed log returns for volatility. This preserves the researched signal; the proxy is not the exchange settlement benchmark.
- One capped immediate-or-cancel order; limit no more than signal ask plus 1¢, rounded to the market tick ladder. No GTC fallback, repeated submission or partial-fill top-up. Hold fills to settlement.
- One open position per asset, four contracts total, $3.80 reserved exposure cap. New entries must fit a $5 net daily loss budget including open risk, measured by Eastern settlement day. These are entry controls, not guaranteed account-wide loss limits; concurrent manual or unrelated bot trades can change account exposure.
- Actual fill prices and fees determine P&L. A limit order can receive an improved price below the entry band during a fast price drop; that is recorded as a warning.

The historical test was promising, but no rule passed every live-validation gate. Single-attempt IOC execution can capture fewer signals than the research simulation. Live profitability remains unproven.

## Operation

Run these commands on the server:

```bash
systemctl --user status kalshi-crypto-z2-live.service
journalctl --user -u kalshi-crypto-z2-live.service -n 50 --no-pager
```

To block new entries while continuing reconciliation and settlement accounting:

```bash
touch /home/varmak/kalshi_revalidation_2026_09_12/live_v1/state/HALT
```

Removing this pilot HALT file permits entries again if all other guards pass. Leave the legacy cushion halt and disabled legacy crypto configuration in place. They are checked before each live entry.

The SQLite ledger is `state/orders.sqlite3`; UI heartbeat is `state/status.json`. Never delete or reset the ledger to recover an uncertain order. The engine searches for its persisted client order ID and blocks further entries until reconciliation resolves it. A process lock prevents observer and live runner from sharing the ledger simultaneously. Observation uses a client that suppresses all mutating requests.

## Verification

28 engine and signal tests passed on the server, covering timeout/restart recovery, duplicate fills, partial fills, delayed fill indexing, YES/NO pricing, actual fees, exposure guards, stale signals, activation removal, and completed-bar selection. Read-only account preflight passed with no crypto positions or resting orders. Streamlit panel test passed, and the integrated dashboard was verified in the browser with a fresh observer heartbeat and zero order attempts.

Real exchange order acceptance and a complete live fill-to-settlement cycle have not been tested. Tests use mocked order execution.

The previous dashboard is backed up on the server as `dashboard.rollback.py` in this deployment directory. Only the existing dashboard presentation was changed; legacy trading code, configuration and halt files were preserved.
