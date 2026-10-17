#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == ashish-home ]]
backup_uuid=$(findmnt -rn -T /var/backups/openstaff-ats/archives -o UUID)
[[ "$backup_uuid" == 90e9596d-883c-4cea-971f-2b34d903b0fe ]]
archive=/root/migration-cleanup-package-config
mkdir -p -m 700 "$archive"
for oldfile in /etc/apt/apt.conf.d/52openstaff-unattended-upgrades /etc/apt/sources.list.d/docker.sources /etc/apt/sources.list.d/cloudflared.list; do
 if [[ -f "$oldfile" ]]; then mv "$oldfile" "$archive/"; fi
done
if dpkg-query -W -f='${Status}' cloudflared 2>/dev/null | grep -q '^install ok installed$'; then
 apt-get purge -y cloudflared
fi
printf 'Unused Docker/OpenStaff package configuration removed. Backup mount unchanged.\n'
