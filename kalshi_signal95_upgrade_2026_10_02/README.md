# Activated live upgrade: signal <95c, execution <=93.9c

**Activated by the user on October 2, 2026.** All46 deployment tests and preflight passed. Subsequent read-only verification confirmed all seven production source hashes match this package and the heartbeat was fresh, LIVE/armed and free of errors. See `activation_receipt.json`. The agent prepared the package and verified activation; the user ran `--execute`.

Only the completed-minute signal ask ceiling changes from <93c to <95c. The order cap remains signal ask +1c capped at93.9c, with the original tick rounding. The z>=2 threshold, volatility/reference inputs, 3–10-minute window, freshness, bounded retries, one IOC attempt, five-contract size, max8 contracts, $7.60 exposure and $10 daily loss including open risk remain unchanged. No extra risk reserve is needed because execution remains capped at93.9c.

Four production files are eligible for replacement: `market_data.py`, `engine.py`, `runner.py` and `dashboard_panel.py`. The latter two changes display the correct signal and execution limits. The package does not replace sizing, retry policy, credentials, mutable ledgers, arm files or HALT flags. It preserves the September28 checkpoint.

## Validation and readiness

The engine and scanner match the tested `signal95` version in `../kalshi_ceiling_95_test_2026_10_02/`. That study contains68 portfolio replays and188 validation-test executions across four variants. Read the research report for modeled profit and missing-depth limitations.

The deployment package passed all46 tests on the server using the bot's actual `/home/varma/kalshi/venv/bin/python` environment. The read-only precheck passed: source hashes matched production, live heartbeat was healthy, and no crypto positions, resting orders or unresolved ledger records existed at the check. Readiness is rechecked at activation, so this snapshot is not a guarantee of later readiness. A local-system-Python attempt lacked PyYAML; no local dependency was installed because validation succeeded in the actual deployment environment.

## Activation procedure (completed)

The user ran this command from the Mac:

```sh
ssh -i ~/.ssh/id_ed25519_migration_192_168_3_136 varma@192.168.3.136 '/home/varma/kalshi/venv/bin/python /home/varma/kalshi_signal95_upgrade_2026_10_02/activate_signal95.py --execute'
```

The activation script first runs tests, verifies that neither production nor staged source has drifted, and requires a healthy already-armed live bot with no unresolved exposure. It backs up the four affected files, stops the service, checks again for a raced-in order, replaces only those files, runs the existing preflight and restarts the service. It then requires a fresh healthy heartbeat showing <95c signal,93.9c execution,size5,max8,$7.60/$10 and writes `activation_receipt.json`.

If an order or position is unresolved, wait for ordinary settlement/reconciliation and rerun. The script does not cancel or close it. A pre-restart failure restores the previous source and resumes the prior service. If the new service starts but fails heartbeat verification, the script reports that and does not blindly roll back because new orders may already exist.

Default invocation or `--check` runs tests and read-only readiness checks only. During preparation, the agent ran **only `--check`**; the user subsequently completed `--execute`. No automatic activation, delayed activation, or new background service is configured.

`original_hashes.json`, `staged_hashes.json` and `upgrade.diff` make the exact source change reviewable. Any separately staged portfolio expansion still requires rebasing against the new production hashes before use.
