LIVE DEPLOYMENT UPDATE — 2026-10-09 11:06 ET
User explicitly requested deployment. Only runner.py was installed and the
existing trading service restarted. Independently verified11:07ET: LIVE, all
14 enabled asset/lane scans present, Copper blocked in both lanes, no errors,
unchanged size5/shared25/$25/$20/rules, dashboard healthy. See parent
deployment_receipt.json, deployment_verification.json and DEPLOYMENT.txt.
The rest of this document records the original staging state and rationale.

ORIGINAL STAGING RECORD
Prepared 2026-10-09 against the exact running eight-asset source snapshot.

Only runner.py differs among production modules. Keep the user's Copper pause,
the current dashboard, ledger, activation state, z/price/time rules, sizes, routes,
ownership and shared risk limits. Do not run an older release installer.

Changes:
1. Cache Coinbase completed bars only after all required 61 minute closes exist
   and are finite and positive. Retry a temporarily incomplete response.
2. Apply the existing scoped GET timeout budget to crypto public-data chains as
   well as noncrypto. Individual requests receive the remaining budget. This
   is not a hard wall-clock guarantee against DNS/OS or streaming-read delays.
3. Isolate asset public-data exceptions and retry incomplete crypto inputs within
   existing 24-check/25-second bounds. Account failures remain globally blocking.
4. Display authoritative filled/settled state instead of an old attempted label.

Validation: 171 offline tests pass (163 existing + 8 resilience tests). The same
8 resilience tests against the captured old runner fail 5, reproducing the gaps;
that comparison is intentional and is not the candidate test result.
All network/credentials/order submission in those tests are mocked.
No real orders, live source edits, activation files, service restarts or transfers.

Reproduce on the server from this directory:
/home/varma/kalshi/venv/bin/python -m unittest test_resilience test_engine test_signals test_portfolio test_retry test_z3 test_execution_improvements test_zero_fill test_multiasset test_wti test_capacity25

The capacity25 regression suite uses its original fixed fixtures in this research
folder's parent: inputs.json, observer.jsonl and evidence/. These are inherited
offline test fixtures, not the current Oct9 audit inputs. Other current audit
evidence is in the parent folder. candidate_manifest.json identifies the source.

At original staging, deployment was a separate step: recheck source hashes, reconcile
orders/account state and preserve state/HALT_COPPER before preparing activation.
Subsequent authorized deployment used parent deploy_resilience.py, which changes
only runner.py and preserves all activation/HALT files and the live database.
Do not rerun it: the original-source precondition is now intentionally superseded.
There is no measured extra-trade claim: these demonstrated failure modes did not
explain the recent crypto decline in the recorded scan history.
