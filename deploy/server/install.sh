#!/usr/bin/env bash
# One-time setup of the pull-based release timer. Run ON THE SERVER, from the folder that holds these files:
#   sudo bash install.sh [linux-user-that-owns-/opt/investment, default: ubuntu]
set -eu
USER_NAME="${1:-ubuntu}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for tool in curl python3 flock docker sha256sum tar; do command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 1; }; done
id "$USER_NAME" >/dev/null
docker ps >/dev/null || { echo "docker is not usable as root"; exit 1; }
install -d -o root -g root -m 0755 /opt/investment/bin   # root-owned: the timer runs this as root
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0755 /opt/investment/releases /opt/investment/state
install -o root -g root -m 0755 "$HERE/auto-deploy.sh" /opt/investment/bin/auto-deploy.sh
install -m 0644 "$HERE/investment-deploy.service" /etc/systemd/system/investment-deploy.service
install -m 0644 "$HERE/investment-deploy.timer" /etc/systemd/system/investment-deploy.timer
systemctl daemon-reload
systemctl enable --now investment-deploy.timer
systemctl list-timers investment-deploy.timer --no-pager
echo
echo "Installed. Look at what it does with:  journalctl -u investment-deploy -n 50 --no-pager"
echo "Run it once now with:                  sudo systemctl start investment-deploy.service"
