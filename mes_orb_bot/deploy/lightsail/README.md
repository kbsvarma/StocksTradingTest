# MES ORB Bot — Lightsail Deployment

Running the bot on AWS Lightsail (or any Ubuntu 24.04 LTS server).
Ireland (eu-west-1) location is fine — the bot connects to IBKR servers
over the internet; network latency is irrelevant for a 1-minute bar strategy.

## Instance Spec

Same stack as the Polymarket bot:

| Setting | Value |
|---|---|
| Provider | AWS Lightsail |
| Region | Europe (Ireland) / eu-west-1 |
| OS | Ubuntu 24.04 LTS |
| RAM / CPU | 2 GB / 2 vCPU |
| Storage | 60 GB SSD |

## Architecture

Two systemd services run permanently:

```
ibgateway.service          — IB Gateway via IBC, always on, auto-restarts
mes-orb-scheduler.service  — scheduler.py, sleeps until 08:45 ET each day,
                             then starts main.py, restarts on crash
```

IB Gateway is a GUI app. On a headless server, `ibgateway.service` starts an
Xvfb virtual display (:99) before launching Gateway so it has a screen to
draw to.

## First-Time Setup

SSH into the Lightsail instance, then:

```bash
# 1. Clone the repo
git clone <your-repo-url> ~/StocksTradingTest
cd ~/StocksTradingTest/mes_orb_bot/deploy/lightsail

# 2. Run the bootstrap (installs IB Gateway, IBC, venv, systemd services)
bash install_mes_bot.sh

# 3. Write IBKR paper credentials into IBC config
bash enter_credentials_linux.sh

# 4. Start IB Gateway and confirm it logs in
sudo systemctl start ibgateway
sudo journalctl -u ibgateway -f
# Look for: "Login has completed" in the IBC log

# 5. Start the scheduler
sudo systemctl start mes-orb-scheduler
sudo journalctl -u mes-orb-scheduler -f
```

## Day-to-Day Operations

```bash
# Service status
sudo systemctl status ibgateway mes-orb-scheduler

# Live logs
sudo journalctl -u ibgateway -f
sudo journalctl -u mes-orb-scheduler -f

# Next scheduled bot run
~/StocksTradingTest/mes_orb_bot/.venv/bin/python \
    ~/StocksTradingTest/mes_orb_bot/scheduler.py --status

# Bot trade logs (today)
ls -lt ~/StocksTradingTest/mes_orb_bot/logs/bot_*.log | head -3

# Restart Gateway (e.g. after IBKR session timeout)
sudo systemctl restart ibgateway

# Stop everything cleanly
sudo systemctl stop mes-orb-scheduler ibgateway
```

## Secrets

Credentials live in `~/IBC/config.ini` (mode 600, not committed to git).
To update credentials:
```bash
bash ~/StocksTradingTest/mes_orb_bot/deploy/lightsail/enter_credentials_linux.sh
sudo systemctl restart ibgateway
```

## Troubleshooting

**Gateway doesn't log in:**
- Check `~/IBC/logs/ibc-*.txt` for the full IBC diagnostic log
- Make sure credentials are set: `grep IbLoginId ~/IBC/config.ini`
- IBKR may require 2FA on first login — you'll need to SSH-forward a display
  or temporarily log in manually via VNC

**"Can't find jars folder":**
- IB Gateway is not installed at the expected path
- Run `find ~/Jts -name "jars" -type d` to locate actual install
- Re-run `install_mes_bot.sh` which will detect and symlink it

**Bot can't connect to Gateway (port 4002):**
- Gateway may be starting up — wait 60–90s after `systemctl start ibgateway`
- Check Gateway accepted the API connection in its logs
- Confirm `ALLOW_CONNECTIONS=yes` is in IBC config.ini

**Xvfb display issues:**
- `DISPLAY=:99 xdpyinfo` should return display info if Xvfb is running
- `pgrep -a Xvfb` to confirm the process exists
