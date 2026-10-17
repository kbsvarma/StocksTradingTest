Kalshi order-book/fill WebSocket migration — 2026-10-15

User scope: shadow alongside existing trader, then switch; unchanged strategy,
price caps, 5 contracts and risk limits. No paid Pyth feed. Deployment outcome and
exact timings are recorded in REPORT.txt and receipts/deployment_receipt.json.

Production: /home/varma/kalshi_revalidation_2026_09_12/live_v1
Trading unit: kalshi-crypto-z2-live.service (systemd --user)
Python: /home/varma/kalshi/venv/bin/python
Migration source: candidate/{stream_transport,market_data,engine,runner}.py
No order was placed by validation/deployment scripts. The authorized live runner
remains the only order writer. No deposits/transfers/ARM/HALT changes.

Data path:
- Authenticated free orderbook_delta and fill subscriptions only. Channel
  allowlist rejects Pyth, CF and all other subscriptions. Public/minute underlying
  sources are unchanged. Explicit use_yes_price=true prevents a future default
  change from reversing NO prices.
- Full snapshot replaces local Decimal book; deltas update it. Sequence tracking
  includes subscription OK messages as well as snapshots/deltas. Wrong/missing
  timestamp, stale source, gaps/duplicates, negative depth or a lost connection
  invalidate the cache. Ping/pong liveness <=2s; source lag <=1s; refresh snapshots
  every10s, expire after25s. Crossed books also fall back. Old symbols are pruned.
- Scanner and final execution check use valid local books, otherwise their existing
  bounded REST reads. Completed-minute signal inputs, one-second retry spacing,
  24checks/25s, original-lane priority and max3 confirmed-zero IOC attempts remain.
- Fill messages wake the main loop; no receiver thread writes the ledger or sends
  an order. Streamed fill rows replace a fills GET only when their identity,
  route, side, total quantity, cash and fees match a fresh terminal REST order.
  Incomplete or inconsistent receipt sets fall back to REST. Verified terminal
  partial fills can be accounted; no top-up. WS absence never proves zero fills.
  Zero-fill retries still require the original REST proof. Fresh account/risk
  checks and order submission remain REST.

Validation evidence:
- receipts/shadow_first*: initial read-only probe found control-ACK sequence
  consumption; corrected before any live change. Keep its failure evidence.
- receipts/shadow_validated*: REST brackets before/after request were too narrow
  to distinguish network-time updates; superseded by explicit time-aligned checks.
- receipts/shadow_final*: controlled reconnect plus actual market rollover, all
  seven enabled assets, full sequence/source checks and quiet REST comparisons.
- receipts/snapshot_probe*, snapshot_audit*, protocol_replay*: a fresh snapshot
  need not equal the immediately preceding reconstructed book. Full snapshots
  can introduce changes not separately delivered as preceding deltas. The entire
  recorded sequence replays correctly with snapshot replacement. Deliberately
  ignoring refreshed snapshots fails with negative depth. Snapshot refresh counts
  are telemetry, not errors; do not reinstate the invalid equality assumption.
- receipts/shadow_aligned*: compare REST top10 with actually observed WS states
  within500ms before /110ms after REST receipt, plus stricter quiet brackets.
  Keep moving/unmatched observations rather than labeling them fills.
- receipts/historical_fill_replay.json: actual historical REST fill economics
  replayed through notification guard for eight assets. This is NOT observation
  of new live WS fill events. Actual notifications, if any, are separately reported.
- receipts/final_tests.log and final_paper_tests.log: offline adversarial and
  existing regression suites. Simulated financial requests use fakes only.

Resting-paper compatibility:
Only its source-integrity guard recognizes the reviewed exact operational
revision (or original for rollback). Frozen config/source, prices, fees, queue
model, every record and cutoff remain. Unknown/mixed source revisions still
block. Its live-comparator statistics must be segmented at migration timestamp.
Record planned restart and health in deployment receipt. Tennis, dashboard,
execution observer and canceled weather process state are preserved.

Rollback:
Stop only trader and resting paper. Restore engine.py, market_data.py, runner.py
from this package's backup to production; remove new stream_transport.py. Restore
backup/paper_observer.py to resting experiment's observer.py and remove only its
reviewed_live_source_hashes.json. Restart trader and paper, verify health/config.
NEVER restore the ledger snapshot or change ARM/HALT files. The consistent DB
backup is evidence/recovery material, not a rollback of subsequent trades.
