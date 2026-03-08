#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-web}"
shift || true

cd /app

case "$MODE" in
  web)
    exec python -m mu_cli.web "$@"
    ;;
  cli)
    exec python -m mu_cli.cli "$@"
    ;;
  models)
    exec python -m mu_cli.cli --list-models "$@"
    ;;
  *)
    exec "$MODE" "$@"
    ;;
esac
