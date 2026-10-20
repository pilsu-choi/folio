#!/usr/bin/env bash
# Compose E2E: bring the stack up, ingest a PDF, wait until the job succeeds, tear down.
#   deploy/compose/e2e.sh            # uses deploy/compose/.env if present, otherwise a throwaway env
#   KEEP=1 deploy/compose/e2e.sh     # leave the stack running
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../e2e-common.sh"

ENV_FILE=${ENV_FILE:-$here/.env}
if [ ! -f "$ENV_FILE" ]; then
  ENV_FILE=$(mktemp)
  # Random throwaway secrets (alphanumeric = URL-safe); never persisted.
  for k in POSTGRES_PASSWORD RABBITMQ_PASSWORD MINIO_ROOT_PASSWORD; do echo "$k=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> "$ENV_FILE"; done
  echo "COMPOSE_PROFILES=infra,cpu" >> "$ENV_FILE"
  TMP_ENV=$ENV_FILE
fi
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-folio-e2e}
dc=(docker compose --env-file "$ENV_FILE" -f "$here/docker-compose.yml" ${COMPOSE_EXTRA:+-f "$COMPOSE_EXTRA"})
cleanup() { [ "${KEEP:-0}" = 1 ] || "${dc[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; }
trap 'cleanup; [ -z "${TMP_ENV:-}" ] || rm -f "$TMP_ENV"' EXIT

port=${FOLIO_API_PORT:-8080}
if [ -n "${NO_BUILD:-}" ]; then "${dc[@]}" up -d --no-build; else "${dc[@]}" up -d --build; fi
basic_scenario "http://127.0.0.1:${port}"
log "compose E2E passed"
