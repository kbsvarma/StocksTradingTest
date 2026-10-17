#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == ashish-home ]]
[[ -f /home/varma/migration_2026_09_25/cold_restore_receipt.json ]]
[[ -f /home/varma/MIGRATION_HOLD ]]
grep -qx 'ReadOnlyApi=yes' /home/varma/ibc/config.ini
for unit in trendbot-xvfb.service trendbot-ibc.service; do
 mkdir -p /etc/systemd/system/$unit.d
 cat > /etc/systemd/system/$unit.d/migration-readonly.conf <<'EOF'
[Unit]
ConditionPathExists=
ConditionPathExists=/home/varma/migration_2026_09_25/cold_restore_receipt.json
EOF
done
systemctl daemon-reload
systemctl start trendbot-xvfb.service trendbot-ibc.service
systemctl is-active trendbot-xvfb.service trendbot-ibc.service
printf 'Gateway only started for read-only reconciliation. Trading services remain held.\n'
