Kalshi WebSocket fill-confirmation repair — 2026-10-15

User-authorized scope: fix fill confirmation and verify actual WebSocket fills;
keep strategy, price limits, five contracts and risk limits unchanged.

Production source: /home/varma/kalshi_revalidation_2026_09_12/live_v1
Service: kalshi-crypto-z2-live.service (user systemd)
Installed at 07:34:42 ET. Deployment receipt and 330-test log are in receipts/.
Verified on four actual fills at 08:13:54 ET, including EURUSD NO 5 at 91c with
the original action=sell message. Both independent WS receipts and exchange
economics match. The unchanged NO frame fails the old guard and passes the fix.
See REPORT.txt and receipts/final_verification.json. Temporary witness stopped.
Changed only stream_transport.py, engine.py, runner.py. The stable Sept28 source
checkpoint, all entry/risk configuration and ARM/HALT flags are preserved.

Fix
The canonical outcome_side (yes/no) and book_side (bid/ask) must agree with the
stored entry and its fresh terminal REST order. Do not infer entry direction
from legacy action. Current NO orders can appear as action=sell / book_side=ask.
Optional purchased_side/client IDs must also agree; exchange/subaccount, total
quantity, cost and fees must match exactly. Explicit closing/partial-sale action
or conflicting directions still fall back to REST. Missing or incomplete WS
receipts never prove a zero fill. Zero-fill retry proof remains REST.

Observability
state/ws_fill_receipts.jsonl retains actual received fill frames and confirmation
decisions with explicit fallback reasons. A bounded asynchronous writer performs
the disk writes; the execution path only queues the audit record. Status exposes
written/dropped/error/pending counts. No credentials are written to this log.
Initial fill transport/time are retained even if settlement reconciliation later
falls back to REST after a process restart.

Validation
330 tests include legacy NO sells, old NO buys, YES buys, canonical direction
mismatches, identity/route/subaccount/fee/quantity checks, partials, duplicates,
zero fills, exact preserved book logic and initial transport through settlement.
Test suites use fake financial endpoints. The deployment preflight uses GETs
only; only the already-authorized trader can place orders under unchanged rules.

Actual verification
capture_fills.py is an independent receive-only subscription to the free fill
channel; it has no exchange/order client and no market subscription. Its temporary
unit is kalshi-ws-fill-witness-20261015.service (maximum four hours).
verify_actual.py compares primary production raw WS messages to that independent
listener, the production ledger, and fresh authenticated GET-only order/fill
receipts. It reports actual success only after a new natural NO/legacy-sell entry
was initially confirmed from WS with identical quantities, cost and fees.
Run verify_actual.py --wait-seconds 55 for a bounded review while the witness is
running. Never generate a verification order or relax an entry gate.
receipts/actual_verification.json records the latest outcome and measured timings.
Stop the temporary witness after success. A deployed fix is not by itself proof
of actual WS fill verification; refer to the report and receipt.

Paper compatibility and unrelated services
Only the resting-paper exact reviewed-source manifest was updated to the new
three hashes, retaining the prior scanner hash. Frozen source/config/fees/queue
rules and all66 existing paper order records remain. Trader479 order records and
484 attempts were retained exactly. Trader and paper observer were restarted in
a quiet flat window; dashboard, execution observer, tennis and weather unchanged.

Rollback
Inspect current state first. In a quiet window, stop the trader and paper observer.
Restore only backup/{stream_transport.py,engine.py,runner.py} into production and
backup/paper_manifest.json to the paper experiment's reviewed_live_source_hashes.json.
Restart the trader and paper observer and verify fresh status/config/guards.
NEVER restore the backed-up orders.sqlite3 or historical ARM/HALT flags. Do not
discard new trades, attempts or actual WS receipts. The backup database is an
audit/recovery snapshot, never a normal code-rollback input.
