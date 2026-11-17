#!/usr/bin/env bash
# NeonReplay – Installation auf Ubuntu 24.04 (Hetzner). Als root ausführen.
set -euo pipefail
REPO="${REPO:-https://github.com/ahmad-onenukta/neonreplay.git}"

apt-get update -y
apt-get install -y ca-certificates curl git ufw cron
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi

# Firewall: SSH + Dashboard
ufw allow OpenSSH && ufw allow 8080/tcp && ufw --force enable

# 4 GB Swap als Puffer für Whisper
if ! swapon --show | grep -q swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

mkdir -p /opt && cd /opt
[ -d neonreplay ] || git clone "$REPO" neonreplay
cd neonreplay && git pull --ff-only || true
mkdir -p data
if [ ! -f .env ]; then
  cp .env.example .env
  IP=$(curl -s -4 ifconfig.me || hostname -I | awk '{print $1}')
  sed -i "s#http://SERVER_IP:8080#http://$IP:8080#; s#^DASHBOARD_PASSWORD=.*#DASHBOARD_PASSWORD=#" .env
  echo "Dashboard: http://$IP:8080  (beim ersten Aufruf Login festlegen)"
fi
docker compose up -d --build < /dev/null

# Auto-Update: alle 10 Min neue Version von GitHub holen und nur bei Änderungen neu bauen
cat > /usr/local/bin/neonreplay-update <<'UPD'
#!/bin/bash
cd /opt/neonreplay || exit 0
export HOME=/root
git config --global --add safe.directory /opt/neonreplay
OLD=$(git rev-parse HEAD); git fetch -q origin main && git reset -q --hard origin/main || exit 0
[ "$OLD" != "$(git rev-parse HEAD)" ] && docker compose up -d --build >> /var/log/neonreplay-update.log 2>&1
UPD
chmod +x /usr/local/bin/neonreplay-update
# systemd-Timer (zuverlässig, ohne cron) – alle 5 Min
cat > /etc/systemd/system/neonreplay-update.service <<'SVC'
[Unit]
Description=NeonReplay Auto-Update
[Service]
Type=oneshot
ExecStart=/usr/local/bin/neonreplay-update
SVC
cat > /etc/systemd/system/neonreplay-update.timer <<'TMR'
[Unit]
Description=NeonReplay Auto-Update alle 5 Minuten
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
TMR
systemctl daemon-reload
systemctl enable --now neonreplay-update.timer
echo "$(date) install ok" >> /var/log/neonreplay-update.log
docker compose ps < /dev/null
