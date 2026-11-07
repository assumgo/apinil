#!/usr/bin/env bash
# Prepara um servidor Ubuntu/Debian e publica uma tag do repositório.
# Uso, no servidor: sudo ./scripts/prepare-server.sh v0.1.0
set -euo pipefail

REF="${1:-main}"
APP_DIR="${APP_DIR:-/opt/apinil}"
REPO="${REPO:-https://github.com/assumgo/apinil.git}"
PORT="${PORT:-80}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute como root: sudo PORT=80 $0 $REF" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git

if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y docker.io docker-compose-v2
fi
systemctl enable --now docker

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO" "$APP_DIR"
fi

cd "$APP_DIR"
git fetch --tags origin
git checkout "$REF"
PORT="$PORT" docker compose up -d --build
docker compose ps
echo "Apinil ${REF} publicado em ${APP_DIR}, porta ${PORT}."
