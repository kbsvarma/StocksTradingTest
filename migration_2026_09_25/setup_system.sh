#!/usr/bin/env bash
set -euo pipefail
if [[ $(id -u) != 0 ]]; then echo 'Run with sudo.' >&2; exit 1; fi
if [[ $(hostname) != ashish-home ]]; then echo 'Wrong destination host.' >&2; exit 1; fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y xvfb xauth default-jre-headless libxrender1 libxtst6 libxi6 libxext6 libfontconfig1 fonts-dejavu-core
loginctl enable-linger varma
printf 'Destination system dependencies installed; user services persist after logout. No trading services started.\n'
