#!/usr/bin/env bash
# Reconstruye credentials.json desde la variable GOOGLE_CREDENTIALS_JSON
# (igual que en GitHub Actions) solo si no se montó ya el archivo.
set -euo pipefail

CRED_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-/app/credentials.json}"

if [ -n "${GOOGLE_CREDENTIALS_JSON:-}" ] && [ ! -f "$CRED_PATH" ]; then
  printf '%s' "$GOOGLE_CREDENTIALS_JSON" > "$CRED_PATH"
fi

export GOOGLE_APPLICATION_CREDENTIALS="$CRED_PATH"

exec "$@"
