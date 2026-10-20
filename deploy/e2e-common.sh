#!/usr/bin/env bash
# Shared E2E helpers (sourced by compose/e2e.sh and kind/e2e.sh). Requires curl and python3.
: "${E2E_PDF:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/tests/fixtures/digital.pdf}"
: "${E2E_TIMEOUT:=300}"

log() { printf '[e2e] %s\n' "$*" >&2; }
die() { log "FAIL: $*"; exit 1; }

# wait_http URL [timeout]
wait_http() {
  local url=$1 t=${2:-$E2E_TIMEOUT} i=0
  until curl -fs "$url" >/dev/null 2>&1; do
    i=$((i + 2)); [ "$i" -ge "$t" ] && die "timeout waiting for $url"; sleep 2
  done
}

# ingest BASE_URL -> prints job_id
ingest() {
  local out
  out=$(curl -fsS -X POST -F "file=@${E2E_PDF}" "$1/v1/ingest") || die "POST /v1/ingest failed"
  python3 -c 'import json,sys; print(json.loads(sys.argv[1])["job_id"])' "$out"
}

# job_status BASE_URL JOB_ID
job_status() {
  curl -fsS "$1/v1/jobs/$2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
}

# wait_job BASE_URL JOB_ID : succeeds only on status=succeeded
wait_job() {
  local base=$1 id=$2 i=0 s
  while :; do
    s=$(job_status "$base" "$id" 2>/dev/null || echo unknown)
    case "$s" in
      succeeded) log "job $id succeeded"; return 0 ;;
      failed|partial) die "job $id ended as $s" ;;
    esac
    i=$((i + 2)); [ "$i" -ge "$E2E_TIMEOUT" ] && die "job $id timeout (last status: $s)"; sleep 2
  done
}

# basic_scenario BASE_URL
basic_scenario() {
  [ -f "$E2E_PDF" ] || die "sample PDF not found: $E2E_PDF"
  wait_http "$1/readyz"
  wait_job "$1" "$(ingest "$1")"
}
