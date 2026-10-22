#!/usr/bin/env bash
# Instala o site estático da Apinil neste servidor.
# Uso: PORT=80 ./scripts/install.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker não está instalado. No Ubuntu: sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2" >&2
  exit 1
fi

docker compose up -d --build
docker compose ps
echo "Site no ar na porta ${PORT:-8080}."
