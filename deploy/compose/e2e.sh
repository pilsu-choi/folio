#!/usr/bin/env bash
# Compose E2E: bring the stack up, ingest a PDF, wait until the job succeeds, tear down.
#   deploy/compose/e2e.sh            # uses deploy/compose/.env if present, otherwise a throwaway env
#   KEEP=1 deploy/compose/e2e.sh     # leave the stack running
#   AIRFLOW=1 deploy/compose/e2e.sh  # also run the folio_ingest DAG (profile `airflow`, DockerOperator) - FR-15
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../e2e-common.sh"

ENV_FILE=${ENV_FILE:-$here/.env}
if [ ! -f "$ENV_FILE" ]; then
  ENV_FILE=$(mktemp)
  # Random throwaway secrets (alphanumeric = URL-safe); never persisted.
  for k in POSTGRES_PASSWORD RABBITMQ_PASSWORD MINIO_ROOT_PASSWORD; do echo "$k=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> "$ENV_FILE"; done
  echo "COMPOSE_PROFILES=${E2E_PROFILES:-infra,dev}" >> "$ENV_FILE"  # dev = heuristic structure server (no GPU/models)
  TMP_ENV=$ENV_FILE
fi
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-folio-e2e}
dc=(docker compose --env-file "$ENV_FILE" -f "$here/docker-compose.yml" ${COMPOSE_EXTRA:+-f "$COMPOSE_EXTRA"})
if [ "${AIRFLOW:-0}" = 1 ]; then
  export DOCKER_GID=${DOCKER_GID:-$(stat -c %g /var/run/docker.sock)}
  export FOLIO_DOCKER_NETWORK=${COMPOSE_PROJECT_NAME}_default
  # Append to the env file's profiles: a CLI --profile would replace them.
  profiles=${COMPOSE_PROFILES:-$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE" | tail -1)}
  export COMPOSE_PROFILES=${profiles:+$profiles,}airflow
fi
cleanup() { [ "${KEEP:-0}" = 1 ] || "${dc[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; }
trap 'cleanup; [ -z "${TMP_ENV:-}" ] || rm -f "$TMP_ENV"' EXIT

port=${FOLIO_API_PORT:-8080}

# Upload the sample to S3, trigger the DAG (scheduler + executor path), then check the chunks reached OpenSearch.
airflow_scenario() {
  local bucket=${FOLIO_BUCKET:-folio-artifacts} key=airflow-e2e/digital.pdf src="e2e://airflow/$RANDOM" i=0 n s
  local run="e2e-$RANDOM" af=("${dc[@]}" exec -T airflow airflow)
  until "${dc[@]}" exec -T airflow airflow dags list -o plain 2>/dev/null | grep -q folio_ingest; do
    i=$((i + 5)); [ "$i" -ge "$E2E_TIMEOUT" ] && die "airflow did not load folio_ingest"; sleep 5
  done
  "${dc[@]}" exec -T airflow airflow dags list-import-errors -o plain 2>/dev/null | grep -q folio_ingest \
    && die "folio_ingest has import errors"
  "${dc[@]}" run --rm --no-deps -T -v "$E2E_PDF:/e2e/in.pdf:ro" --entrypoint python api -c \
    "import boto3,os; boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL']).upload_file('/e2e/in.pdf', '$bucket', '$key')"
  "${af[@]}" dags trigger folio_ingest -r "$run" \
    -c "{\"uri\": \"s3://$bucket/$key\", \"source_uri\": \"$src\"}" >/dev/null 2>&1 || die "airflow dags trigger failed"
  i=0
  while :; do
    s=$("${af[@]}" dags list-runs folio_ingest -o plain 2>/dev/null | awk -v r="$run" '$2 == r {print $3}')
    case "$s" in
      success) break ;;
      failed) die "DAG run $run failed" ;;
    esac
    i=$((i + 5)); [ "$i" -ge "$E2E_TIMEOUT" ] && die "DAG run $run timeout (last state: ${s:-none})"; sleep 5
  done
  log "DAG run $run succeeded"
  n=$("${dc[@]}" exec -T api python -c "import json,os,urllib.request as u; b=os.environ['OPENSEARCH_URL']
u.urlopen(u.Request(b + '/contracts/_refresh', method='POST'))
q=json.dumps({'query': {'term': {'source_uri': '$src'}}}).encode()
r=u.urlopen(u.Request(b + '/contracts/_count', q, {'Content-Type': 'application/json'}))
print(json.load(r)['count'])")
  [ "$n" -gt 0 ] || die "no chunks indexed for $src"
  log "airflow DAG indexed $n chunks (source_uri=$src)"
}

if [ -n "${NO_BUILD:-}" ]; then "${dc[@]}" up -d --no-build; else "${dc[@]}" up -d --build; fi
basic_scenario "http://127.0.0.1:${port}"
[ "${AIRFLOW:-0}" = 1 ] && airflow_scenario
log "compose E2E passed"
