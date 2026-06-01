#!/bin/bash
# Daily in-place log rotation for the webull bot launchd logs.
# In-place truncation (keeps inode) so launchd's open append handle stays valid.
# Keeps the last MAXLINES lines of each log; runs after market close.
set -u
LOGDIR="/Users/varmakammili/Documents/GitHub/StocksTradingTest/webull_bot/logs"
MAXLINES=5000
for f in launchd.out launchd.err rsync-ec2.err; do
  L="$LOGDIR/$f"
  [ -f "$L" ] || continue
  # only rotate if over ~5MB
  sz=$(stat -f%z "$L" 2>/dev/null || echo 0)
  if [ "$sz" -gt 5000000 ]; then
    tmp="$(tail -n $MAXLINES "$L")"
    printf '%s\n' "$tmp" > "$L"
  fi
done
