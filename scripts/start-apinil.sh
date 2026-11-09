#!/bin/sh
# Sobe o site e a área restrita no mesmo processo.
set -eu
cd "$(dirname "$0")/.."
if ! python3 -c "import psycopg" >/dev/null 2>&1; then
  python3 -m pip install --disable-pip-version-check -r auth/requirements.txt
fi
export BIND="${BIND:-0.0.0.0}"
exec python3 auth/app.py
