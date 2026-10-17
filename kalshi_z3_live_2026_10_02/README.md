# Strong-cushion z3 release — LIVE at FIVE contracts, October 2, 2026 ET

**Activated by the user at 22:16 ET and independently verified live.** Both original z2 and strong-cushion z3 are enabled at five contracts. All eight production source hashes match the tested release; the heartbeat is fresh, both services are running without restarts, and the dashboard returns HTTP 200. No halt or error was present. See `activation_receipt.json` and `activation_verification.json`. The user changed the new z3 lane from one to five contracts before activation. The prior one-contract staging package is preserved in `archive_one_contract_stage_20261002.tar.gz` for audit only. No production source, arming state, service, ledger, or funding was changed while preparing this revision.

## Configuration

| | Original z2 | Strong cushion z3 |
|---|---:|---:|
| Contracts per market | 5 | **5** |
| Minimum cushion | 2 | 3 |
| Signal ask | 85–<95¢ | 93–<98¢ |
| Maximum buy | 93.9¢ | 97.5¢ |
| Remaining time | 3–10 minutes | 3–10 minutes |
| Maximum spread | 2¢ | 2¢ |
| Fee-inclusive reserve per trade | $4.75 | **$5.00** |

The new lane's own ceilings scale proportionally to 20 contracts / $20 exposure / $25 daily loss including open risk. **The stricter shared account ceilings remain 20 contracts / $19 exposure / $10 daily loss including open risk. The lane ceiling does not add another $25 loss allowance.** With zero realized P&L that day, one full position from each lane uses $9.75 of the $10 daily allowance; two z3 positions use exactly $10. Prior losses reduce headroom and realized profits can increase it. Four z3 positions cannot fit the shared $19 exposure ceiling.

Authenticated staging checks found **$49.7534 available**, no crypto positions, no resting orders and no unresolved ledger entries. No transfer is needed to activate this configuration.

BTC, ETH, SOL and XRP remain the only assets. Entry rules, completed-minute volatility/reference inputs, the 35-second deadline, bounded book rechecks and hold-to-settlement behavior are unchanged. Five contracts must be displayed inside the execution cap before submission. There is no smaller-size fallback; an IOC can nevertheless partially fill because the book changes. Any partial fill is held and reconciled without topping up.

One execution process and one durable market-keyed ledger serve both lanes. Original z2 gets the first opportunity each scan pass, and its same-asset book retries finish before z3 proceeds. A recorded attempt prevents either lane from making another attempt in that market. One active market per asset, shared risk, ambiguous-order reconciliation and global HALT remain enforced. The new arm is `ARM_Z3_5CT` containing `5`; an old one-contract arm cannot enable this release. `HALT_Z3` blocks only new z3 entries while settlement reconciliation continues.

## Five-contract validation

All **77 tests pass in the actual server Python/Streamlit runtime**. New tests verify five-contract book depth, multi-level fills, order payload count, mixed-lane risk, two-z3-position risk and rejection of old one-contract arming. Existing tests cover partial fills, unknown orders, settlement losses, UI history attribution, source guards, rollback before restart and preserving any new orders after restart.

Recomputed historical scanner validation matched **5,722 decisions**, including 952 qualifying z3 minute signals and 1,676 qualifying z2 minute signals, from saved raw completed Coinbase closes. No forming-minute bars were used. The frozen September stable checkpoint is unchanged.

September 12–October 1 UTC, using the actual staged engine's risk checks, assumed next-minute quote fills, modeled fees and an extra 1¢ per contract:

| Portfolio | Trades | Wins / losses | Net | Drawdown |
|---|---:|---:|---:|---:|
| Existing z2 alone, five contracts | 245 | 241 / 4 | $61.4996 | $4.6711 |
| Both lanes, five contracts each | **302** | **298 / 4** | **$65.9172** | $4.6711 |
| Original z2 portion | 240 | 236 / 4 | $59.8830 | $4.6711 |
| New z3 portion | 62 | 62 / 0 | $6.0342 | $0 |

There are 57 more entries overall and $4.4176 more modeled net than original z2 alone; five original entries are displaced. Larger z3 positions bind shared risk more often: this is 62 z3 entries versus 79 in the previous one-contract combined replay. These results were recalculated at five-contract sizing and fee rounding, not obtained by multiplying old profit by five. Running original z2 alone through the staged engine exactly preserves its previous market list and results.

A separate continuous August 13–September 11 recorded-book replay of z3 alone under the same shared account ceilings required full five-contract displayed depth. It produced **192 trades, 190 wins, two losses, +$12.7769 net and $9.6707 drawdown** after modeled fees and 1¢/contract stress. There were 301 depth-supported minute opportunities at five contracts versus 312 at one contract; the daily budget rejected 29 five-contract opportunities. This is not the joint portfolio result.

September lacks historical depth proof. Displayed books do not guarantee fills; network latency, failed IOC attempts and exact retry timing are not modeled. These are exploratory historical simulations, not a promise of profit or absence of losses. Evidence: `deployment_replay.json`, `five_contract_depth.json`, `historical_validation.json`.

## Activation record and recovery

```bash
ssh -i ~/.ssh/id_ed25519_migration_192_168_3_136 varma@192.168.3.136 '/home/varma/kalshi/venv/bin/python /home/varma/kalshi_z3_live_2026_10_02/activate.py --execute'
```

The user already ran the command above successfully; **do not rerun it on this activated release**. Its original-source guards intentionally reject a second installation. Before activation, default execution and `--check` only validated readiness using GET requests.

Activation requires unchanged production hashes, passing tests, a healthy original service and a flat/reconciled account. It stops the existing execution service, acquires its process lock, rechecks account state, preserves the ledger and takes an audit snapshot, installs four updated source files plus `lanes.py`, validates imports and then creates only the new z3 arm before restarting. It never restores a snapshot over the working ledger. Failure before restart restores the original source; uncertain health after restart does not trigger unsafe rollback over potentially new trades. It writes `activation_receipt.json` only after a fresh heartbeat verifies both five-contract lanes and all caps. Check that receipt and current live status before declaring activation complete.

If an original position opens before activation, wait for normal settlement and rerun. Preserve all ledger and HALT state. To stop z3 entries later, create `state/HALT_Z3` or remove `state/ARM_Z3_5CT`; open orders continue reconciling. Do not restore an older engine while any z3 orders or positions remain unresolved.

Dashboard: [Crypto dashboard](http://192.168.3.136:8507). The selectors are **All crypto**, **Original z2 · 5 contracts**, and **Strong cushion z3 · 5 contracts**, with separate actual trades, fees, wins, losses and P&L. Historical untagged records remain original z2. The combined paper research service remains independent and unchanged.
