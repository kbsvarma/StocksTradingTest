USD/CAD only — prepared October 14, 2026

STATUS: LIVE — activated by the user and independently verified 2026-10-14T13:28:47.291363-04:00.
The user narrowed the request from USD/CAD + AUD/USD to USD/CAD only.
The preparation below preserved production; the subsequent user-run activation
installed the two planned modules and armed USD/CAD. See deployment_receipt.json
and live_verification.json. Do not rerun activation; its receipt guard rejects it.

The user-run activation command is:
ssh -i ~/.ssh/id_ed25519_migration_192_168_3_136 varma@192.168.3.136 '/home/varma/kalshi/venv/bin/python /home/varma/kalshi_usdcad_2026_10_14/activate.py --execute'

Without --execute, activate.py runs offline fake-exchange tests and GET-only
market/source/account checks. It never creates an arm flag or restarts services.
With --execute, it requires the account and ledger flat, preserves a consistent
audit backup, changes only lanes.py and dashboard_panel.py, creates the separate
ARM_USDCAD_5CT marker and restarts the existing trader, dashboard and read-only
execution observer. It checks all effective settings and preserved historical
records. Open/pending positions cause a clean exit; rerun after settlement.
Source drift, existing activation markers and interrupted starts require review.
It never places orders directly, transfers money or restores historical ledgers.
After restart the existing trading runner may place live orders for USD/CAD.
Successful completion writes deployment_receipt.json; that receipt and fresh
runtime state are required before claiming the lane is live.

USD/CAD profile (both existing strategies):
  Original: z >= max(0.5, 1.75 + 0.5*log2(minutes/5)).
  Strong cushion: z >= max(0.5, 2.75 + 0.5*log2(minutes/5)).
  Both: 1–10 minutes remaining; five contracts; 61 completed Pyth minute prices,
  market opening reference, and exact asset/market-route validation.
  Original signal: 85–<95c; absolute execution cap 93.9c.
  Strong cushion signal: 93–<98c; absolute execution cap 97.5c.
  Signal spread <=2c; existing signal+1c order limit and downward tick rounding.
  Current USD/CAD ticks are whole cents, so effective maximum orders are 93/97c.
  Full five-contract displayed depth required; actual IOC fills are not guaranteed.
  One owned market across the strategies; no partial-fill top-up. Existing bounded
  same-signal zero-fill retry applies. The special WTI later-signal retry stays WTI-only.

Preserved:
  All seven incumbent assets, all their profiles (including original BTC z3),
  five contracts per entry, all other signal/time/price/execution rules;
  shared 25 contracts/$25 exposure/$20 daily loss including open risk;
  premium 25/$25/$25 subordinate to shared limits;
  Copper retired/halted, AUD/USD deferred, weather paper stopped/disabled,
  tennis paper unchanged, September 28 checkpoint unchanged.

Public API confirmed exchange_index=0 for USD/CAD, same as EUR/USD. GET-only
balances at preparation: route0 USD112.3090, route2 USD64.9104. No transfer needed.
Current EUR/USD is enabled, so its existing display-only FX cash refresh already
covers the shared USD/CAD balance. Every order independently refreshes its route's
cash in Engine.account; runner.py does not need to change.

Research: ../kalshi_finance_expansion_2026_10_14/REPORT.txt. Frozen adaptive replay
had 68 trades (67 wins/1 loss), +$8.1910 after modeled fees/friction over 6.77 days.
The shared-cap replay added 65 trades and +$7.3586 over 6.48 days, with no incumbent
trade displaced in that replay. These are historical simulations, not guaranteed
fills or a statistically established edge. No new optimization was done here.

Verification:
  235 offline tests pass (activation_tests.log and test_command.json).
  Historical two-contract test_scaling.py remains archived and excluded exactly
  as in the previous 214-test release; current capacity/portfolio suites cover
  five-contract behavior. Old recorded-book fixtures copied without changes.
  BTC profile regression now compares to the immediate current z3 baseline.
  Source warmup test expects the additional USD/CAD request after existing assets.
  New tests cover separate arming, route0 funding, dynamic thresholds, whole-cent
  caps, shared risk, ownership, late arm removal, source finality and UI rules.
  Initial broad discovery output is preserved in server_tests.log; it included
  obsolete two-contract tests, missing copied fixtures and a test field-name typo.
  No production code was changed to make those obsolete assumptions pass.
  Public source probe passed with all61 required completed inputs.
  Staged Streamlit render passed for all markets (16 rule rows) and USD/CAD-only
  view (2 rule rows), without exceptions. Preview shows USD/CAD unarmed truthfully.
  Source hashes, all existing flags and five service PIDs unchanged; all12 stable
  checkpoint files match; weather remains inactive. See prepared_verification.json.

Files:
  original/                    Exact production snapshot, immutable comparison.
  candidate/                   Staged production modules + offline tests.
  original_hashes.json          Deployment drift guard.
  candidate_manifest.json      Exact candidate/source/test hashes.
  deployment_preflight.json    GET-only funding/source/config/services checks.
  source_probe.json             Public source/market/order-book receipt.
  prepared_verification.json   UI render and production preservation evidence.
  activate.py                  User-run final activation; default check only.

Dashboard remains http://192.168.3.136:8507 . After activation it gains a USD/CAD
card and filter, live public prices and entry-window timing, both strategy rule
rows and separate results. FX cash is labeled EUR/USD + USD/CAD to avoid implying
that the common route balance belongs to EUR/USD alone.
