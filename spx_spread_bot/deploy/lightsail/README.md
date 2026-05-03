# SPX Spread Bot — Lightsail Deployment

This follows the same style as your existing cloud bot deployment:

- AWS Lightsail
- Ubuntu 24.04 LTS
- `systemd` services
- local-only Streamlit dashboard via SSH tunnel

## Services

- `spx-ibgateway.service` (IB Gateway + IBC + Xvfb)
- `spx-spread-bot.service` (main bot process)
- `spx-spread-dashboard.service` (Streamlit UI)

## Bootstrap

```bash
git clone <repo-url> ~/StocksTradingTest
cd ~/StocksTradingTest/spx_spread_bot/deploy/lightsail
bash install_spx_bot.sh
bash enter_credentials_linux.sh
```

Start services:

```bash
sudo systemctl start spx-ibgateway
sudo systemctl start spx-spread-bot
sudo systemctl start spx-spread-dashboard
```

Check status:

```bash
sudo systemctl status spx-ibgateway spx-spread-bot spx-spread-dashboard
sudo journalctl -u spx-ibgateway -f
sudo journalctl -u spx-spread-bot -f
sudo journalctl -u spx-spread-dashboard -f
```

Dashboard tunnel:

```bash
ssh -L 8503:127.0.0.1:8503 ubuntu@<lightsail-host>
# open http://127.0.0.1:8503
```

## Important

- Keep `config.yaml` in paper mode until you explicitly decide to cut over.
- Validate market data entitlements and options permissions before first session.
- Ensure the server IP is in IBKR trusted IP settings.
