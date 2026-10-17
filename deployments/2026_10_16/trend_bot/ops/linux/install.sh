#!/bin/bash
# ONE-TIME sudo install for the trend_bot stack (mirrors the kalshi pattern)
set -euo pipefail
sudo apt-get update -qq && sudo apt-get install -y -qq xvfb
sudo cp ~/trendbot/units/trendbot-*.service ~/trendbot/units/trendbot.service \
        ~/trendbot/units/trendbot-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trendbot-xvfb.service
# NOTHING ELSE STARTS NOW: trendbot-ibc + trendbot + watchdog.timer +
# dashboard are enabled at CUTOVER (never run two gateways at once).
echo "install done — xvfb running; remaining units start at cutover"
