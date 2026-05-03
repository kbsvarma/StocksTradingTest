#!/usr/bin/env bash
# install_mes_bot.sh — Full bootstrap for MES ORB bot on Ubuntu 24.04 LTS
#
# Run once on a fresh Lightsail instance:
#   git clone <repo> ~/StocksTradingTest
#   cd ~/StocksTradingTest/mes_orb_bot/deploy/lightsail
#   bash install_mes_bot.sh
#
# After this script finishes:
#   1. Run enter_credentials.sh to write your IBKR paper credentials
#   2. sudo systemctl start ibgateway   → verify Gateway logs in
#   3. sudo systemctl start mes-orb-scheduler

set -euo pipefail

BOT_USER="${USER}"
BOT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IBC_DIR="${HOME}/IBC"
GW_INSTALL_DIR="${HOME}/Jts/ibgateway/1045"
IBC_VERSION="3.23.0"
IBC_ZIP_URL="https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip"
GW_INSTALLER_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"

echo ""
echo "======================================================"
echo "  MES ORB Bot — Lightsail Bootstrap"
echo "  Bot root : ${BOT_ROOT}"
echo "  User     : ${BOT_USER}"
echo "======================================================"
echo ""

# ── 1. System packages ─────────────────────────────────────────────────────────
echo "[1/7] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    xvfb x11-utils xterm \
    wget curl unzip \
    git \
    2>/dev/null
echo "✓ System packages installed"

# ── 2. Python virtual environment ─────────────────────────────────────────────
echo "[2/7] Setting up Python venv…"
python3 -m venv "${BOT_ROOT}/.venv"
"${BOT_ROOT}/.venv/bin/pip" install --quiet --upgrade pip
"${BOT_ROOT}/.venv/bin/pip" install --quiet ib_insync pytz streamlit altair pandas
echo "✓ Python venv ready at ${BOT_ROOT}/.venv"

# ── 3. IB Gateway Linux standalone ────────────────────────────────────────────
echo "[3/7] Installing IB Gateway standalone (Linux)…"
if [[ -d "${GW_INSTALL_DIR}/jars" ]]; then
    echo "✓ IB Gateway already installed at ${GW_INSTALL_DIR}"
else
    mkdir -p "${HOME}/Jts/ibgateway"
    GW_INSTALLER="/tmp/ibgateway-installer.sh"
    echo "  Downloading IB Gateway (~300 MB)…"
    wget -q --show-progress -O "${GW_INSTALLER}" "${GW_INSTALLER_URL}"
    chmod +x "${GW_INSTALLER}"
    echo "  Running installer (silent, target: ${GW_INSTALL_DIR})…"
    # install4j silent install: -q for quiet, -dir for target directory
    bash "${GW_INSTALLER}" -q -dir "${GW_INSTALL_DIR}"
    rm -f "${GW_INSTALLER}"

    if [[ ! -d "${GW_INSTALL_DIR}/jars" ]]; then
        # Some versions install without version subdir — find and symlink
        FOUND=$(find "${HOME}/Jts" -name "jars" -type d 2>/dev/null | head -1)
        if [[ -n "$FOUND" ]]; then
            ACTUAL_DIR="$(dirname "${FOUND}")"
            echo "  Gateway installed at ${ACTUAL_DIR}, symlinking to ${GW_INSTALL_DIR}…"
            mkdir -p "$(dirname "${GW_INSTALL_DIR}")"
            ln -sfn "${ACTUAL_DIR}" "${GW_INSTALL_DIR}"
        else
            echo "ERROR: IB Gateway installer completed but jars/ not found. Check manually."
            exit 1
        fi
    fi
    echo "✓ IB Gateway installed at ${GW_INSTALL_DIR}"
fi

# ── 4. IBC ─────────────────────────────────────────────────────────────────────
echo "[4/7] Installing IBC ${IBC_VERSION}…"
if [[ -f "${IBC_DIR}/gatewaystart.sh" ]]; then
    echo "✓ IBC already installed at ${IBC_DIR}"
else
    mkdir -p "${IBC_DIR}"
    IBC_ZIP="/tmp/ibc-linux.zip"
    wget -q --show-progress -O "${IBC_ZIP}" "${IBC_ZIP_URL}"
    unzip -qo "${IBC_ZIP}" -d "${IBC_DIR}"
    rm -f "${IBC_ZIP}"
    chmod +x "${IBC_DIR}"/*.sh "${IBC_DIR}/scripts/"*.sh 2>/dev/null || true
    echo "✓ IBC installed at ${IBC_DIR}"
fi

# ── 5. Configure IBC ───────────────────────────────────────────────────────────
echo "[5/7] Configuring IBC gatewaystart.sh…"
GW_SCRIPT="${IBC_DIR}/gatewaystart.sh"
# IB Gateway on Linux stores the bundled JRE path in pref_jre.cfg
PREF_JRE_CFG="${GW_INSTALL_DIR}/.install4j/pref_jre.cfg"
if [[ -f "${PREF_JRE_CFG}" ]]; then
    JRE_HOME="$(cat "${PREF_JRE_CFG}")"
    JAVA_PATH="${JRE_HOME}/bin"
else
    JAVA_PATH="${GW_INSTALL_DIR}/.install4j/jre.bundle/Contents/Home/bin"
fi
if [[ ! -f "${JAVA_PATH}/java" ]]; then
    # Fallback: search for java binary in i4j_jres
    JAVA_PATH="$(find "${HOME}/.local/share/i4j_jres" -name "java" -type f 2>/dev/null | head -1 | xargs -I{} dirname {})"
fi

sed -i "s|^TWS_MAJOR_VRSN=.*|TWS_MAJOR_VRSN=1045|"    "${GW_SCRIPT}"
sed -i "s|^TWS_PATH=.*|TWS_PATH=~/Jts|"               "${GW_SCRIPT}"
sed -i "s|^IBC_PATH=.*|IBC_PATH=${IBC_DIR}|"           "${GW_SCRIPT}"
sed -i "s|^IBC_INI=.*|IBC_INI=${IBC_DIR}/config.ini|"  "${GW_SCRIPT}"
sed -i "s|^TRADING_MODE=.*|TRADING_MODE=paper|"        "${GW_SCRIPT}"
sed -i "s|^LOG_PATH=.*|LOG_PATH=${IBC_DIR}/logs|"       "${GW_SCRIPT}"
if [[ -n "${JAVA_PATH}" ]]; then
    sed -i "s|^JAVA_PATH=.*|JAVA_PATH=${JAVA_PATH}|"   "${GW_SCRIPT}"
fi

mkdir -p "${IBC_DIR}/logs"

# Write config.ini if not already present
if [[ ! -f "${IBC_DIR}/config.ini" ]]; then
cat > "${IBC_DIR}/config.ini" <<'INI'
IbLoginId=
IbPassword=
TradingMode=paper
AcceptNonBrokerageAccountWarning=yes
AcceptBidAskLastSizeDisplayUpdateNotification=accept
ReadOnlyLogin=no
FIX=no
INI
    chmod 600 "${IBC_DIR}/config.ini"
    echo "✓ IBC config.ini created (credentials empty — run enter_credentials.sh next)"
else
    echo "✓ IBC config.ini already exists"
fi
echo "✓ IBC configured"

# ── 6. Systemd services ────────────────────────────────────────────────────────
echo "[6/7] Installing systemd services…"
SYSTEMD_SRC="${BOT_ROOT}/deploy/systemd"

for SVC in ibgateway mes-orb-scheduler mes-orb-dashboard; do
    SVC_FILE="${SYSTEMD_SRC}/${SVC}.service"
    if [[ ! -f "${SVC_FILE}" ]]; then
        echo "WARNING: ${SVC_FILE} not found — skipping"
        continue
    fi
    # Substitute placeholders
    sudo cp "${SVC_FILE}" "/etc/systemd/system/${SVC}.service"
    sudo sed -i "s|__BOT_USER__|${BOT_USER}|g"    "/etc/systemd/system/${SVC}.service"
    sudo sed -i "s|__BOT_ROOT__|${BOT_ROOT}|g"    "/etc/systemd/system/${SVC}.service"
    sudo sed -i "s|__IBC_DIR__|${IBC_DIR}|g"      "/etc/systemd/system/${SVC}.service"
    sudo sed -i "s|__HOME__|${HOME}|g"            "/etc/systemd/system/${SVC}.service"
    echo "  ✓ /etc/systemd/system/${SVC}.service"
done

sudo systemctl daemon-reload
sudo systemctl enable ibgateway mes-orb-scheduler mes-orb-dashboard
echo "✓ Services installed and enabled at boot"

# ── 7. Bot logs dir ────────────────────────────────────────────────────────────
echo "[7/7] Creating logs directory…"
mkdir -p "${BOT_ROOT}/logs"
echo "✓ ${BOT_ROOT}/logs"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Bootstrap complete!"
echo ""
echo "  NEXT STEPS (in order):"
echo ""
echo "  1. Enter IBKR paper credentials:"
echo "     ${BOT_ROOT}/deploy/lightsail/enter_credentials_linux.sh"
echo ""
echo "  2. Start IB Gateway and verify it logs in:"
echo "     sudo systemctl start ibgateway"
echo "     sudo journalctl -u ibgateway -f"
echo ""
echo "  3. Once Gateway is running, start the scheduler + dashboard:"
echo "     sudo systemctl start mes-orb-scheduler mes-orb-dashboard"
echo "     sudo journalctl -u mes-orb-scheduler -f"
echo ""
echo "  4. Open dashboard (SSH tunnel from your Mac):"
echo "     ssh -L 8502:127.0.0.1:8502 ubuntu@34.247.209.179"
echo "     Then browse: http://127.0.0.1:8502"
echo ""
echo "  5. Check next scheduled bot run:"
echo "     ${BOT_ROOT}/.venv/bin/python ${BOT_ROOT}/scheduler.py --status"
echo "======================================================"
echo ""
