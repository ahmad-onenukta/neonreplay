#!/usr/bin/env bash
# NeonReplay – Installation auf Ubuntu 24.04 (Hetzner). Als root ausführen.
set -euo pipefail
REPO="${REPO:-https://github.com/ahmad-onenukta/neonreplay.git}"

apt-get update -y
apt-get install -y ca-certificates curl git ufw
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
  PW=$(openssl rand -base64 12 | tr -d '/+=')
  sed -i "s#http://SERVER_IP:8080#http://$IP:8080#; s#bitte-aendern#$PW#" .env
  echo "=============================================="
  echo " Dashboard: http://$IP:8080"
  echo " Login:     noman / $PW   (steht auch in /opt/neonreplay/.env)"
  echo "=============================================="
fi
docker compose up -d --build
docker compose ps
