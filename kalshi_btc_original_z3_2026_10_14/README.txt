BTC original-lane z3 — October 14, 2026

User authorization: change only original BTC z from 2 to 3; other lanes, five contracts, price and risk limits unchanged.

Trading change is two lines in lanes.py: the existing per-asset lane_for lookup overrides z=3 for BTC/cushion_z2 only. Scanner, final engine check and current UI rules use this same lookup. Lane identifiers and the original global z2 default stay unchanged for historical ownership and all other assets. Premium BTC already uses z3 and remains unchanged.

Reporting-only companion: operations_view.py uses each recorded signal's required_z when present, preventing the new threshold from retroactively removing previously qualified BTC signals from the execution funnel. Records without this field use the current rule. This module does not drive trading.

214 offline tests pass, including six new cases for per-asset/profile parity, inferred historical asset identity, scanner rejection below three, final-order rejection independent of scanner, effective status and historical UI classification. All non-z fields match the captured original profiles for all eight historical assets/both lanes. Existing risk, ownership, source, price, zero-fill and WTI retry tests retained. Shared fake qualified BTC signal is adjusted from 2.5 to 3.5; historical recorded signals themselves are unchanged and those below three now assert rejection. Historical test_scaling.py targets retired two-contract/eight-contract configuration, as documented by the prior deployment, and is excluded from the current 214-test command. Initial broad-discovery failures are retained in initial_discovery_tests.log; missing evidence fixtures were copied unchanged from the prior test package. No tests are deployed to production.

Deployment uses GET-only account checks, requires flat reconciled ledger/exchange, verifies all source and stable checkpoint hashes, preserves flags, takes a consistent audit backup, and installs only lanes.py and operations_view.py. It restarts the existing trader, dashboard and read-only execution observer to load current rules; tennis/weather paper services remain untouched. No credentials, funding, account orders, activation flags or mutable ledger are copied or manually changed.

Unchanged: 5 contracts/entry; original signal85–<95c and absolute buy cap93.9c, premium applicable bands and97.5c cap; original BTC3–10min; shared25 contracts/$25 exposure/$20 daily loss including open risk; premium25/$25/$25 subordinate to shared limits; all other asset thresholds/time profiles; one owned market across lanes; existing IOC/retry/WTI settings. Copper stays retired.

Research basis: ../kalshi_loss_review_2026_10_14/REPORT.txt. This is the user-selected loss-reduction candidate, not proof of higher long-run returns or zero future losses. The September28 stable checkpoint remains immutable. Original BTC z2 can be restored later through a separately authorized state-preserving source change; never restore old ledgers or arming files.

Deployment status and exact verification time are recorded in deployment_receipt.json and verification.json. Source originals, test command/output, candidate manifest and server backup path are retained. Paper-review preservation baselines may show expected production hash/PID differences from this authorized deployment; compare the receipts, do not rewrite experiment baselines.
