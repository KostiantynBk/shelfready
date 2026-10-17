#!/usr/bin/env bash
# Public API for the Vercel demo on a small Ubuntu 24.04 server: Docker + Caddy with HTTPS on <ip>.sslip.io.
# Run as root: curl -fsSL https://raw.githubusercontent.com/KostiantynBk/shelfready/main/infra/deploy.sh | bash
# Optional: put LLM_MODE=cached and LLM_API_KEY=... in /root/shelfready/.env (chmod 600) to let "Write copy" call the model.
set -euo pipefail
IP="$(curl -fsS https://api.ipify.org)"
DOMAIN="${DOMAIN:-${IP//./-}.sslip.io}"

command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
if [ "$(free -m | awk '/Mem:/ {print $2}')" -lt 2000 ] && ! swapon --show | grep -q swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi
cd /root
[ -d shelfready ] || git clone https://github.com/KostiantynBk/shelfready
cd shelfready && git pull --ff-only
DOMAIN="$DOMAIN" docker compose --profile public up -d --build api caddy
for _ in $(seq 1 60); do curl -fsS "https://$DOMAIN/healthz" >/dev/null 2>&1 && break; sleep 5; done
curl -fsS "https://$DOMAIN/healthz" && echo && echo "API ready: https://$DOMAIN"
