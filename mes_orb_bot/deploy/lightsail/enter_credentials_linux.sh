#!/usr/bin/env bash
# enter_credentials_linux.sh — Writes IBKR paper credentials into IBC config.
# Uses Python for safe substitution (handles $, ~, and other special chars).

CONFIG="${HOME}/IBC/config.ini"

if [[ ! -f "$CONFIG" ]]; then
    echo "ERROR: $CONFIG not found. Run install_mes_bot.sh first."
    exit 1
fi

echo ""
echo "Enter your IBKR paper trading credentials."
echo "(Stored in ${CONFIG} — readable only by you)"
echo ""

read -r  -p "IBKR username : " USERNAME
read -rs -p "IBKR password : " PASSWORD
echo ""

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
    echo "ERROR: username and password cannot be empty."
    exit 1
fi

python3 - "$CONFIG" "$USERNAME" "$PASSWORD" <<'PYEOF'
import sys, re, pathlib
config_path = pathlib.Path(sys.argv[1])
username    = sys.argv[2]
password    = sys.argv[3]
text = config_path.read_text()
text = re.sub(r'^IbLoginId=.*',  f'IbLoginId={username}',  text, flags=re.MULTILINE)
text = re.sub(r'^IbPassword=.*', f'IbPassword={password}', text, flags=re.MULTILINE)
config_path.write_text(text)
PYEOF

chmod 600 "$CONFIG"
echo "✓ Credentials saved to $CONFIG (mode 600)"
echo ""
echo "Start IB Gateway now:"
echo "  sudo systemctl start ibgateway"
echo "  sudo journalctl -u ibgateway -f"
