# Capacity20 release — user activated and independently verified

**Current status:** user ran activation successfully. Independent verification confirms LIVE/armed, five contracts per market, max20/$19/$10, all source hashes matching, no halt/error, and healthy dashboard. See `activation_receipt.json` and `activation_verification.json`. Earlier staging notes below describe the pre-activation state.

User requested recommendation1: five contracts per order, max20 contracts/$19 reserved exposure, retain$10 daily loss budget. Active Signal95, execution<=93.9c, z2 and bounded pre-submission retries remain. Changes only engine.py (cap label) and sizing.py (two constants). The source is rebased on all seven verified production source hashes.

52 tests and read-only account preflight passed on the server: $49.7534 crypto cash, zero positions/resting/unresolved orders. Current bot and dashboard are healthy. Existing live limits remain max8/$7.60 until the user executes the command below. No money transfer is needed for this preflight.

```sh
ssh -i ~/.ssh/id_ed25519_migration_192_168_3_136 varma@192.168.3.136 '/home/varma/kalshi/venv/bin/python /home/varma/kalshi_capacity20_2026_10_02/activate_capacity.py --execute'
```

The activation script defaults to read-only --check. User execution rechecks source hashes/account/ledger; backs up the two source files; stops the bot; rechecks no unresolved exposure; installs the two files; runs preflight; restarts and requires a fresh healthy heartbeat showing20/$19/$10. It never copies or resets ledger, credential, HALT or arm files. Failure before restart restores original source. Failure after restart reports uncertain activation rather than blindly rolling back over possible new orders. Receipt is activation_receipt.json on the server.

Keeping $10 daily loss including open risk initially permits two full5ct positions ($9.50 reserved), not four. Higher daily realized profit can make room for additional positions within the20/$19 ceiling. The dashboard reads these settings from the live status and needs no source change.
