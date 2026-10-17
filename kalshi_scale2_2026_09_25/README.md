# Two-contract release — September 25, 2026

Staged only. The running bot remains unchanged until the user executes activate.py --execute.

Sizing: 2 contracts per market, 8 total contracts, $7.60 exposure cap, $10 daily loss budget including open risk, $2 minimum free cash, $10 preflight cash requirement. Signal thresholds, assets, price caps, timing, and one-attempt-per-market behavior remain unchanged.

Scanner and final order-book checks require depth for two contracts within the existing price limit. New ledger records retain requested_count=2. Existing records without that field remain one-contract orders. Partial fills are reconciled without topping up. Settlement uses actual filled quantity, actual cost, and exchange-reported fees. The dashboard reads sizing from status rather than hardcoded labels.

Validation: 45 offline tests, including two-contract request shape, two-level depth, partial fills, overfills, legacy records, four simultaneous two-contract markets, cash and daily risk limits, and status labels. No real test orders submitted. Read-only account check confirmed $45.0597, no crypto positions, no resting crypto orders, and no legacy safety block. Existing live service still reports one contract.

Run from the Mac:

```sh
ssh varmak@192.168.3.36 '/home/varmak/kalshi/venv/bin/python /home/varmak/kalshi_scale2_2026_09_25/activate.py --execute'
```

The installer verifies release hashes and unchanged production source, runs tests, requires clear current state, stops the service, saves source and SQLite backups, installs the release, runs read-only preflight, creates ARM_2CT, and starts the existing service. It confirms a fresh two-contract heartbeat. Dashboard labels refresh automatically. Original ARM_1CT is retained for compatibility with archived source, but the new runner requires ARM_2CT containing 2.

Failures before starting the new service restore old source and restart the original service. After the new process starts, the installer deliberately does not restore old code or the ledger: two-contract orders may exist and require the new reconciliation code. Inspect status before further action. Backups are for recovery evidence; never overwrite a ledger containing later orders.
