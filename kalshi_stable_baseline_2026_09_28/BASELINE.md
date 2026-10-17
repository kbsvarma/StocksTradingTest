# Accepted stable baseline: Kalshi crypto 15-minute z2

Saved 2026-09-28 at the user's request as the design to return to after experiments. **Stable means the user's accepted operating baseline, not a statistically proven or loss-free strategy.** Preserve this checkpoint; create separate experiment versions. No live changes were made to create it.

## Exact design

- BTC, ETH, SOL, XRP 15-minute prediction markets. No perpetuals or additional assets in this baseline.
- Favorite ask >= $0.85 and < $0.93; spread <= $0.02; 3–10 minutes to close; minimum cushion z = 2.0.
- Decisions use completed minute Kalshi bid/ask quotes. Favorite YES if bid + ask > 1, otherwise NO.
- Spot is the last completed Coinbase minute close. Reference is the exact Coinbase close immediately before market opening, not the official market strike. Missing reference rejects the signal.
- Require all 61 consecutive completed prices to calculate 60 log returns with population standard deviation. No filling missing bars. z = signed (spot/reference - 1) / (sigma * sqrt(minutes remaining)).
- Evidence must remain within 35 seconds of its decision timestamp, including the final pre-submission check.
- Require full intended quantity displayed inside the allowed price cap. Reject a book weakened below 85c. Limit is signal ask + 1c, capped at 93.9c and rounded on the market's tick ladder toward less spending.
- Single immediate-or-cancel order attempt per market. No resting-order maker fallback, no top-up after partial fill, no repeated ambiguous submission. Thus a two-contract request can fill only one.
- Hold to settlement; no early stop/profit-taking rule.
- Current size: 2 contracts/market; max 8 contracts, $7.60 reserved exposure, $10 daily loss budget INCLUDING open risk; one market per asset. Reserve 95c per contract for risk. Minimum free cash $2; activation preflight $10. Sizing lives in source/sizing.py.
- Persist intent before submission. Block entries while reconciliation is unresolved or unexpected crypto resting orders exist. Retain manual HALT and the current activation/funding guards.

## Saved evidence

### Current full design on the earlier August episode

Window Aug 8 03:11 ET through Aug 12 02:00 ET, first old entry minute through final traded market close. Results are reconstructed, not actual live P&L.

| Execution scenario | Trades | W/L | Net at 1 contract | Net at 2 contracts |
|---|---:|---:|---:|---:|
| Subsequent recorded book, sufficient depth within cap | 16 | 16/0 | +$1.4472 | +$2.8950 |
| Next minute opening quote, no depth proof | 22 | 22/0 | +$1.7308 | +$3.4621 |
| Next minute closing quote, delay sensitivity only | 10 | 10/0 | +$0.8501 | +$1.7006 |

The closing-quote scenario is not executable under the real 35-second gate. Book observations arrive 1–25 seconds after decisions; fills are assumed from displayed depth, not guaranteed. All 16 book entries were checked against an unmodified copy of the deployed scanner at both sizes. Fees use the rounded model, not actual historical charged fees. One-contract replay used proportionally scaled exposure/daily budgets; neither risk budget bound in these samples.

Inputs: 1,920 surrounding market records, 55,357 book snapshots, Coinbase history. 29 qualifying minute signals across 22 markets. Excluded 11 price-qualified minutes with incomplete spot history. All four old recorded losses with z >=2 fail today's full entry rules. This does not isolate a single cause of improvement. Only 16 depth-supported trades is a small sample; no inference of zero future losses.

Reproduction/evidence: ../kalshi_current_on_august_2026_09_28/REPORT.md, replay.py, replay_one_contract.py, results.json, results_one_contract.json, old_loss_comparison.json, fresh_data/, books.jsonl.

### Earlier actual August design — do not confuse with this baseline

Aug 8–12 old ledger: 225 records, 198 wins, 21 settlement losses, 6 stopped, net -$37.2616 at mixed historical sizes. Old cushion was 1.5; sizing increased 1 to 2 to 5; daily loss halt disabled; resting orders/96c fallback; changing stop logic. Simply keeping its recorded z >=2 trades yields 31 trades, 27W/4L, -$8.2478 mixed-size or approximately -$1.6086 proportional one-contract P&L. **That subset is NOT a replay of the current design.** The full reconstruction above supersedes it for that question. Old code used different reference/volatility/timing inputs. Ledger figures are not an independent exchange cash reconciliation.

Earlier diagnosis: ../kalshi_restart_audit_2026_09_12/DIAGNOSIS.md. September revalidation: ../kalshi_restart_audit_2026_09_12/RESEARCH_REPORT.md. The pre-live study did not clear every predeclared evidence-volume/statistical gate; do not rewrite that limitation as a passed gate.

### Threshold experiments in the September recorded period

All normalized to two contracts. Recorded lower-threshold signals lack historical book-depth verification; cap-cost assumes fills, next-minute quotes are an alternative sensitivity scenario, not confidence bounds.

- Sep 12–26 cap-cost: z2 91 trades/89W/2L +$9.4650; z1.95 113/109W/4L +$8.8880; z1.9 145/141W/4L +$14.0717; z1.8 206/199W/7L +$17.0423.
- Same period next-minute: z2 +$5.6519; z1.95 +$3.6114; z1.9 +$5.2607; z1.8 +$3.7529. More fills did not show a robust improvement.
- Updated Sep 12–27 cap-cost: z2 100/98W/2L +$10.8217; z1.5 704/654W/50L +$11.3943. Next-minute: z2 +$6.2506 versus z1.5 -$28.5437.
- Actual ledger reference at Sep 26 snapshot: 91 settled, 89 positive/2 negative, +$6.1463, mixed historical one/two-contract sizing. This is a dated snapshot, not today's account total.

Evidence: ../kalshi_threshold_replay_2026_09_26/REPORT.md and results_195.json; ../kalshi_threshold_150_2026_09_27/REPORT.md. Do not compare mismatched periods/sizing as if they were the same experiment. Lowering z has not been accepted into the live baseline.

## Code checkpoint and return procedure

source/ contains exact captured production source and the live systemd unit. manifest.json records SHA-256 hashes and capture time. Server: varma@192.168.3.136. Production: /home/varma/kalshi_revalidation_2026_09_12/live_v1. Service: kalshi-crypto-z2-live.service. Baseline server copy: /home/varma/research_checkpoints/kalshi_stable_baseline_2026_09_28.

This checkpoint deliberately excludes credentials, activation files, HALT flags, and live mutable ledgers. It is a code/configuration recovery reference, not a whole-account backup or automatic activation command.

For a future return: compare manifest/source against the deployed version; preserve the current database and account state; pause new submissions and reconcile pending orders/positions; restore only the intended source/configuration files; run captured tests and current operational preflight; verify effective gates/sizing/dashboard and only then resume under the user's applicable authorization. Never overwrite a live ledger or copy historical arming state as part of restoration. Do not blindly restart with unreconciled orders. The preserved client is a point-in-time dependency and may need review if the exchange API changes.
