#!/usr/bin/env bash
set -euo pipefail

BOT_USER="${USER}"
BOT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IBC_DIR="${HOME}/IBC"
GW_INSTALL_DIR="${HOME}/Jts/ibgateway/1045"
IBC_VERSION="3.23.0"
IBC_ZIP_URL="https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip"
GW_INSTALLER_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"

echo "[1/7] Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3 python3-pip python3-venv \
  xvfb x11-utils xterm \
  wget curl unzip git jq

echo "[2/7] Creating virtualenv"
python3 -m venv "${BOT_ROOT}/.venv"
"${BOT_ROOT}/.venv/bin/pip" install --upgrade pip
"${BOT_ROOT}/.venv/bin/pip" install -r "${BOT_ROOT}/requirements.txt"

echo "[3/7] Installing IB Gateway"
if [[ ! -d "${GW_INSTALL_DIR}/jars" ]]; then
  mkdir -p "${HOME}/Jts/ibgateway"
  GW_INSTALLER="/tmp/ibgateway-installer.sh"
  wget -q --show-progress -O "${GW_INSTALLER}" "${GW_INSTALLER_URL}"
  chmod +x "${GW_INSTALLER}"
  bash "${GW_INSTALLER}" -q -dir "${GW_INSTALL_DIR}"
  rm -f "${GW_INSTALLER}"
fi

echo "[4/7] Installing IBC"
if [[ ! -f "${IBC_DIR}/gatewaystart.sh" ]]; then
  mkdir -p "${IBC_DIR}"
  IBC_ZIP="/tmp/ibc-linux.zip"
  wget -q --show-progress -O "${IBC_ZIP}" "${IBC_ZIP_URL}"
  unzip -qo "${IBC_ZIP}" -d "${IBC_DIR}"
  rm -f "${IBC_ZIP}"
  chmod +x "${IBC_DIR}"/*.sh "${IBC_DIR}/scripts/"*.sh 2>/dev/null || true
fi

echo "[5/7] Configuring IBC"
GW_SCRIPT="${IBC_DIR}/gatewaystart.sh"
sed -i "s|^TWS_MAJOR_VRSN=.*|TWS_MAJOR_VRSN=1045|" "${GW_SCRIPT}"
sed -i "s|^TWS_PATH=.*|TWS_PATH=~/Jts|" "${GW_SCRIPT}"
sed -i "s|^IBC_PATH=.*|IBC_PATH=${IBC_DIR}|" "${GW_SCRIPT}"
sed -i "s|^IBC_INI=.*|IBC_INI=${IBC_DIR}/config.ini|" "${GW_SCRIPT}"
sed -i "s|^TRADING_MODE=.*|TRADING_MODE=paper|" "${GW_SCRIPT}"
sed -i "s|^LOG_PATH=.*|LOG_PATH=${IBC_DIR}/logs|" "${GW_SCRIPT}"

mkdir -p "${IBC_DIR}/logs"

if [[ ! -f "${IBC_DIR}/config.ini" ]]; then
cat > "${IBC_DIR}/config.ini" <<'INI'
IbLoginId=
IbPassword=
TradingMode=paper
ReadOnlyLogin=no
AcceptNonBrokerageAccountWarning=yes
AcceptBidAskLastSizeDisplayUpdateNotification=accept
FIX=no
AllowBlindTrading=yes
StoreSettingsOnServer=no
INI
chmod 600 "${IBC_DIR}/config.ini"
fi

echo "[6/7] Installing systemd services"
SYSTEMD_SRC="${BOT_ROOT}/deploy/systemd"
for SVC in spx-ibgateway spx-spread-bot spx-spread-dashboard; do
  sudo cp "${SYSTEMD_SRC}/${SVC}.service" "/etc/systemd/system/${SVC}.service"
  sudo sed -i "s|__BOT_USER__|${BOT_USER}|g" "/etc/systemd/system/${SVC}.service"
  sudo sed -i "s|__BOT_ROOT__|${BOT_ROOT}|g" "/etc/systemd/system/${SVC}.service"
  sudo sed -i "s|__IBC_DIR__|${IBC_DIR}|g" "/etc/systemd/system/${SVC}.service"
done

sudo systemctl daemon-reload
sudo systemctl enable spx-ibgateway spx-spread-bot spx-spread-dashboard

echo "[7/7] Ensuring data and logs directories"
mkdir -p "${BOT_ROOT}/data" "${BOT_ROOT}/logs"

echo "Install complete. Next:"
echo "  1) bash ${BOT_ROOT}/deploy/lightsail/enter_credentials_linux.sh"
echo "  2) sudo systemctl start spx-ibgateway"
echo "  3) sudo systemctl start spx-spread-bot spx-spread-dashboard"
