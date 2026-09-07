# Advisor: local model and server migration

The terminal, research collectors, source verification and application state run
on the Linux server. Inference uses the provider selected in the private research
configuration: Ollama runs local weights; Gemini calls Google's API. Restoring
Ollama weights does not change that selection. The Mac is a browser and deployment
client. Public sources still need network access; local inference needs no
Gemini/OpenAI key. No personal assistant login is involved.

## Reproducible model installation

On Linux x86_64, as the service user:

```bash
bash advisor/ops/install_local_model.sh
```

This installs checksum-verified Ollama 0.33.3 under
`~/.local/share/advisor/ollama`, pulls Qwen3.5 9B Q4_K_M and verifies its model
digest. The user service listens **only on 127.0.0.1:11434**. It loads one model,
serves one inference at a time, and has CPU/memory limits. CPU fallback works;
GPU performance depends on the destination's drivers and device permissions.
Model weights need about 6.6 GB of disk; leave additional space for runtime,
context memory, backups and source history. A 16 GB GPU is the practical starting
point for this configuration; test actual placement with `/api/ps`.

The installer also accepts `gemma4:12b` as its first argument and verifies its
separately pinned digest (about 7.6 GB of weights). Restore the model selected in
the source host's private research configuration, including the same reasoning and
`ADVISOR_RESEARCH_SAMPLING` setting (`official` or `conservative`);
do not assume that the default model is the one that passed your evaluation.
Installation does not constitute intelligence-quality acceptance.

`qwen3.8:27b` is also pinned for evaluation (approximately 17.7 GB of model files).
It exceeds a 16 GB GPU's available capacity and uses CPU offload on that hardware.
Its inclusion in the installer is reproducibility support, not a claim that it
has passed the investment-research acceptance cases. Preserve the selected
provider and model explicitly during migration.

Put these settings in `~/.advisor_research.env` (mode 600):

```bash
ADVISOR_RESEARCH_PROVIDER=ollama
ADVISOR_RESEARCH_MODEL=qwen3.5:9b
```

Keep the terminal's existing authentication configuration. Model configuration
does not grant permission to access the terminal. Do not expose the Ollama port
to the LAN or Internet.

## Snapshot and restore rehearsal

Finish active investigations first. Use an absolute, private output path:

```bash
python -m advisor.ops.migrate export --repo "$PWD" --bundle "$HOME/advisor-backup.tgz" --include-secrets
python -m advisor.ops.migrate restore --bundle "$HOME/advisor-backup.tgz" --destination "$HOME/advisor-restore-test"
```

Export includes Advisor code, runtime data, watchlists, research reports and
SQLite online backups. It excludes transient job locks, logs and model weights.
SQLite backups include committed WAL transactions. `--include-secrets` also
includes the application configuration files and optional `.advisor_model.env` hardware settings; **the resulting private
archive contains credentials and must only be transferred over SSH**. Omit that
flag for a shareable code/data backup, and configure credentials separately.
Each archive has an external checksum and a full internal file manifest.

Restore checks the archive checksum, safe paths, file inventory, every file
digest and every SQLite database before creating the destination. It refuses an
existing destination and starts no services. Configuration is restored into a
private `.advisor-migration/config` directory for explicit installation.

## Move to a new server

1. Provision Linux x86_64 and Python 3.12 with venv support, curl, zstd and a
   systemd user session. Enable user lingering if services must run after logout.
   Configure GPU drivers/device access on that host if desired; do not copy a
   virtual environment or hardware driver directory from the old host.
2. Rehearse a restore on the destination while the original server still runs.
   Transfer the archive and its `.sha256` file using `scp`. Run the restore module
   from a checkout of the same Advisor release, or copy `advisor/ops/migrate.py`
   and execute it directly with Python.
3. On the restored destination:

   ```bash
   cd "$HOME/advisor-restored"
   python3 -m venv .venv
   .venv/bin/pip install -r advisor/requirements.resolved.lock
   .venv/bin/pip install -r advisor/requirements.test.lock
   .venv/bin/python -m pytest -q advisor/tests
   bash advisor/ops/install_local_model.sh
   .venv/bin/python -m advisor.ops.provision_remote --repo "$PWD" --bind 127.0.0.1 --render-only "$HOME/advisor-units-review"
   ```

   Choose the destination's actual private IP for LAN access. The default binds
   the terminal to loopback. Inspect rendered units and verify model digest
   against `.advisor-migration/model-manifest.json`. No old hostname, user or
   repository directory is required.
4. Before final cutover, stop the source host's Advisor timers and writer
   services, let any investigations finish, then export a **final** snapshot.
   An online snapshot is internally consistent per SQLite database; stopping
   writers is required for one common cutover point across all files/databases.
   Restore this final snapshot into a new destination directory. Never run two
   scheduled writers against divergent copies of the same workspace.
5. Install reviewed units and preserved authentication configuration:

   ```bash
   .venv/bin/python -m advisor.ops.provision_remote --repo "$PWD" --bind NEW_PRIVATE_IP --activate
   ```

   This starts the terminal only. Enable the selected Advisor timers and quote
   services after validating the destination. Other trading bots, broker
   gateways, notification credentials and licensed data feeds are not migrated
   by this Advisor-only bundle. Configure those integrations explicitly if used.
6. Verify terminal authentication, saved watchlist, source timestamps, an actual
   investigation, model provider/digest, and service logs. Keep the old server
   and final archive until acceptance. Rollback means stopping destination
   writers before re-enabling the source; do not overwrite the source's data.

## Health and operation

```bash
systemctl --user status advisor-model advisor-terminal
curl --fail http://127.0.0.1:11434/api/version
curl --fail http://127.0.0.1:11434/api/ps
journalctl --user -u advisor-model -n 30 --no-pager
```

`size_vram: 0` means CPU inference. A successful health endpoint is not a passed
research evaluation: validate source-bound claims and review results across
several companies after a model, quantization, driver or prompt change.

Hardware settings such as `GGML_VK_VISIBLE_DEVICES` are host-specific. Verify GPU enumeration and memory placement on the destination before reusing `.advisor_model.env`.
