#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == ashish-home ]]
[[ -f /home/varma/MIGRATION_HOLD ]]
for src in /home/varma/migration_2026_09_25/trend-stage/units/system/*; do
 unit=$(basename "$src")
 if systemctl is-active --quiet "$unit"; then echo "Refusing active unit $unit"; exit 1; fi
 [[ ! -e /etc/systemd/system/$unit ]]
 grep -q 'ConditionPathExists=!/home/varma/MIGRATION_HOLD' "$src"
done
for src in /home/varma/migration_2026_09_25/trend-stage/units/system/*; do install -m 644 "$src" /etc/systemd/system/; done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/trendbot*.service /etc/systemd/system/trendbot*.timer
printf 'Guarded system units installed, disabled and not started.\n'
