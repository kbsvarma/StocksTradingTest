#!/usr/bin/env bash
# USER-RUN activation: enables real-money orders. Never used by staging/tests.
set -euo pipefail
cd /home/varmak/kalshi_revalidation_2026_09_12/live_v1
if systemctl --user is-active --quiet kalshi-crypto-z2-live.service; then
  echo 'One-contract live service is already running.'
  exit 0
fi
systemctl --user stop kalshi-crypto-z2-observe.service
if ! /home/varmak/kalshi/venv/bin/python runner.py --preflight; then
  systemctl --user start kalshi-crypto-z2-observe.service
  exit 1
fi
printf '1\n' > state/ARM_1CT
systemctl --user disable kalshi-crypto-z2-observe.service
systemctl --user enable --now kalshi-crypto-z2-live.service
systemctl --user --no-pager status kalshi-crypto-z2-live.service
