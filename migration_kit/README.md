# Trend migration kit — Advisor deferred

Prepared September 6, 2026. **Advisor is explicitly excluded while its implementation continues.** This kit neither packages Advisor nor changes its services. It prepares Trend's existing paper daemon, gateway application, nightly shadows and the new prospective shadow recorder for a controlled Linux-to-Linux move.

## What is ready now

A real source snapshot and a restore rehearsal have completed on `ashish-server`:

- Verified warm bundle: `/home/varmak/trend-migration-20260906-v3`.
- 1,235 manifest entries, about 354 MB of file content / 343 MiB on disk.
- Four SQLite files copied through the online backup API and integrity checked; empty legacy `.db` placeholders preserved as files.
- Original Trend commit and clean tracked checkout preserved.
- Restore rehearsal relocated service/wrapper paths to **`/home/migrationtest` / user `migrationtest`**, while keeping the bot checkout and all frozen shadow hashes unchanged.
- No source service was stopped or restarted. No destination service was installed or started.
- Three fixture tests passed: committed WAL backup, refusal of active-source cold capture, and relocation preserving frozen code/lockfiles plus tamper detection.

A previous Mac-only dashboard-log symlink was already dangling on the source. It is documented under `unavailable_links` rather than represented as successfully migrated data. The actual Linux logs are captured.

**This is a warm preparation snapshot, not the final cutover state.** The current bots keep writing data. Individual SQLite backups are consistent; a running snapshot cannot promise a single instant shared by every database, JSON state file and log. The manifest makes that limitation explicit. Final cold capture is required at migration time.

## Bundle contents

- `home/trendbot/trend_bot/`: exact deployed code and Git metadata, configuration, bar stores, state databases/JSON, decision and execution logs. `.venv` and transient lock/cache files excluded; `requirements.lock` retained.
- `home/trendbot/shadow_capture/`: frozen code, model files, seed, epoch settings, full captured evidence, positions, backups and reports. The epoch and model identities are preserved, not reset by moving hosts.
- `home/trendbot/shadow_history/`, nightly launcher, gateway launcher, host wrappers and other Trend support files.
- `home/Jts/ibgateway/`, `home/Jts/jars/`, `home/ibc/`: installed gateway/IBC application payload. Internal symlinks become portable relative links.
- `units/system/` and `units/user/`: actual installed Trend unit files, including BOTH old nightly shadows and new five-minute capture.
- `runtime.lock`: full `pip freeze --all` from the deployed Trend virtual environment, not only top-level package wishes.
- `manifest.json` plus checksums: source host, Python/architecture, code identity, service states, copy methods and per-file hashes.

Credential files are **not in the bundle**: `.trendbot_env`, `ibc/config.ini`, `Jts/jts.ini`, `Jts/xmlopt.dat`. Other user-level gateway authentication caches are outside the selected roots. Provision required private configuration separately over authenticated SSH with owner-only permissions; expect IBKR authentication/2FA on a new host. Never paste their contents into reports. Financial state/logs in the regular bundle are still private: keep the directory/archive owner-only.

Target profile tested by staging: Linux x86_64, Python **3.12** (source 3.12.3). Ubuntu 24.04 is the straightforward choice. Fresh OS/runtime installation, new-host gateway login and broker reconciliation have NOT been tested because no destination has been selected. ARM, macOS or a different Python minor require separate dependency validation.

## Commands

Run these from a copy of this kit. Commands never start services automatically.

### 1. Capture the current source

On the source host, as its normal user:

```sh
python3 migrate.py inventory --output inventory.json
python3 migrate.py snapshot --mode warm --output "$HOME/trend-prep-NEW_TIMESTAMP"
python3 migrate.py verify "$HOME/trend-prep-NEW_TIMESTAMP"
```

Output paths must not already exist and must be outside captured roots. A failed capture receives `INCOMPLETE` and cannot be restored. Capture is Trend-only by construction; there is no Advisor inclusion flag.

### 2. Transfer and render for the new user

Transfer the owner-only bundle and kit with SSH/rsync. On the destination:

```sh
python3 migrate.py verify /path/to/bundle
python3 migrate.py stage /path/to/bundle \
  --destination "$HOME/trend-stage" \
  --target-home "$HOME" --target-user "$USER"
python3 install_staged.py "$HOME/trend-stage"
bash bootstrap_runtime.sh "$HOME/trend-stage" python3.12
```

`install_staged.py` refuses the source hostname, a different effective home, running/enabled destination Trend services, and existing payload directories. It writes a migration receipt without changing the shadow epoch. It does not install units, connect to IBKR or start anything. Bootstrap reconstructs the exact captured environment, runs `pip check` and the bot test suite, and validates frozen shadow hashes. A failed bootstrap leaves `MIGRATION_INCOMPLETE` visible.

OS prerequisites normally include `python3.12-venv`, `xvfb`, `git`, `rsync`, and libraries required by the copied IB Gateway. Install these through the destination's package manager. If offline installation is required, download compatible wheels from the captured lock into `STAGE/wheels` in advance; bootstrap automatically uses that directory without contacting package indexes. A wheelhouse has not been built in this preparation.

The staged unit files are ready to copy into `/etc/systemd/system/` and `~/.config/systemd/user/` after inspection. System units need sudo. Reload managers, but **do not enable/start units during warm preparation**. Enable user lingering on the destination when ready so user timers survive logout. Existing socket/client settings are preserved (paper gateway 4002, shadow data client 146); verify there are no conflicts on the new box.

### 3. Final cold snapshot and cutover — later, not performed now

1. Coordinate the shared gateway move. **Advisor currently uses this gateway too. Moving/stopping it affects Advisor, even though this package excludes Advisor.** Wait for its implementation/migration decision; do not run a second session as a workaround.
2. During the agreed cutover, stop AND disable source Trend writers, timers/watchdogs, dashboards and gateway/Xvfb as appropriate. Account for running one-shot jobs and prevent timer/watchdog restarts. Do not cancel protective broker orders merely to copy files.
3. Run `snapshot --mode cold`. It refuses active/enabled Trend services/timers and verifies their state again after capture. Preserve credentials separately. A cold bundle contains a quiesced cross-file snapshot; it does not independently certify broker account reconciliation.
4. Transfer/verify/stage the final cold bundle into a NEW staging directory. On the stopped destination use `install_staged.py FINAL_STAGE --refresh-cold` to replace the warm preparation. It preserves the prepared virtual environment and separately provisioned IBC config, retains the old payload in a timestamped rollback directory, and refuses warm bundles. Other private files live outside the replaced roots.
5. Install the rendered unit files; confirm the source remains stopped/disabled. Start only the destination gateway/Xvfb first and complete paper-account authentication. Verify broker positions/orders against the migrated state and follow the existing reconciliation/runbook procedures before starting the execution daemon. Do not bypass existing halt or runtime-identity checks.
6. Start the destination paper daemon, dashboard, old nightly timer and new capture timer only after those checks. Verify startup reconciliation, actual received data, model identity, capture continuation and first new records. Restore monitoring/watchdogs last. Do not claim the old host's qualification/soak as new-host evidence.
7. Keep the old host disabled as rollback. If rollback is needed, stop the destination first and reconcile any intervening orders/state; blindly restoring the old pre-move database can duplicate or lose state.

No automated `activate` command is provided: shared-gateway ownership, authentication and reconciliation must be resolved at actual cutover. The repetitive capture, path rendering, payload placement, dependency rebuilding and integrity checks are automated.

## Scope and limitations

The source is untouched except for separate preparation directories. Advisor deployment/config/data are not read into or rewritten by the snapshot. Requirements may still need downloadable wheels and OS libraries on the new host. Credentials and machine authentication are separate. Warm images are not a promise of zero-loss failover. Daily source backups and this preparation do not replace the final stopped-source capture.

The original bot runbooks remain applicable: `trend_bot/docs/EC2_DEPLOYMENT.md`, `trend_bot/ops/linux/README.md`, and the shadow recorder's own `README.md`. This kit makes the move repeatable without weakening their runtime checks.
