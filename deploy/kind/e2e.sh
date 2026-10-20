#!/usr/bin/env bash
# kind E2E: install the chart, run the ingest scenario, then `helm upgrade` while jobs are in flight.
# Images folio:TAG, folio-structure:TAG-heuristic and folio-opensearch:TAG must exist locally (docker build).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
. "$here/../e2e-common.sh"

TAG=${FOLIO_TAG:-0.1.0}
CLUSTER=folio-e2e
NS=${NS:-folio-e2e}
REL=${REL:-folio}
PORT=${LOCAL_PORT:-18080}
HELM=${HELM:-helm}

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config "$here/kind-config.yaml" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

images=("folio:$TAG" "folio-structure:$TAG-heuristic" "folio-opensearch:$TAG"
        postgres:16 rabbitmq:3-management-alpine cgr.dev/chainguard/minio:latest@sha256:cff197fef764642223ef9f4444c903d51aee9b544a7be8bc007fef6b7b00d136)
for i in "${images[@]}"; do
  docker image inspect "$i" >/dev/null 2>&1 || docker pull "$i"
  kind load docker-image --name "$CLUSTER" "$i"
done

# FOLIO_TAG applies to every folio-built image (app, structure, opensearch), not just the app
tags=(--set image.tag="$TAG" --set structure.image.tag="$TAG-heuristic" --set infra.opensearch.image.tag="$TAG")
$HELM upgrade --install "$REL" "$root/deploy/helm/folio" -n "$NS" --create-namespace \
  -f "$root/deploy/helm/folio/values-dev.yaml" "${tags[@]}" --wait --timeout 10m

api="${REL}-api"   # fullname = release name when it contains "folio" (default REL=folio)
kubectl -n "$NS" port-forward "svc/$api" "$PORT:8080" >/dev/null 2>&1 &
pf=$!
trap 'kill $pf 2>/dev/null || true' EXIT
base="http://127.0.0.1:$PORT"
sleep 3

log "scenario 1: install + ingest"
basic_scenario "$base"

log "scenario 2: helm upgrade with jobs in flight"
jobs=()
for _ in 1 2 3 4 5; do jobs+=("$(ingest "$base")"); done
$HELM upgrade "$REL" "$root/deploy/helm/folio" -n "$NS" \
  -f "$root/deploy/helm/folio/values-dev.yaml" "${tags[@]}" \
  --set-string worker.podAnnotations.e2e-rollout="$RANDOM" --set-string api.podAnnotations.e2e-rollout="$RANDOM" \
  --wait --timeout 10m &
up=$!
# The port-forward dies when the api pod is replaced; restart it as needed while polling.
restart_pf() { kill $pf 2>/dev/null || true; kubectl -n "$NS" port-forward "svc/$api" "$PORT:8080" >/dev/null 2>&1 & pf=$!; sleep 2; }
for id in "${jobs[@]}"; do
  i=0
  until [ "$(job_status "$base" "$id" 2>/dev/null || echo unknown)" = succeeded ]; do
    s=$(job_status "$base" "$id" 2>/dev/null || echo unknown)
    case "$s" in failed|partial) die "job $id ended as $s after upgrade" ;; unknown) restart_pf ;; esac
    i=$((i + 2)); [ "$i" -ge "$E2E_TIMEOUT" ] && die "job $id lost/timeout during upgrade"; sleep 2
  done
  log "job $id survived upgrade"
done
wait "$up" || die "helm upgrade failed"
restart_pf  # the api pod may have been replaced after the last job finished
wait_http "$base/readyz" 120
log "kind E2E passed"
