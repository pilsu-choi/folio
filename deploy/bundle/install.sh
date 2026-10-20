#!/usr/bin/env bash
# Install from the offline bundle (no internet needed).
#   ./install.sh                     # docker load + docker compose up -d (images local, FOLIO_REGISTRY empty)
#   ./install.sh --registry REG      # docker load, retag + push to private registry REG, print the helm command
set -euo pipefail
cd "$(dirname "$0")"
REGISTRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --registry) REGISTRY=${2%/}; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

for t in images/*.tar; do docker load -i "$t"; done
VERSION=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')

if [ -z "$REGISTRY" ]; then
  cd compose
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created compose/.env from .env.example. Set POSTGRES_PASSWORD, RABBITMQ_PASSWORD and MINIO_ROOT_PASSWORD, then re-run." >&2
    echo "FOLIO_PROJECT_DIR=../project" >> .env
    [ -d ../models ] && echo "FOLIO_MODELS_DIR=$(cd ../models && pwd)" >> .env
    exit 1
  fi
  docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d
  exit 0
fi

python3 - "$REGISTRY" <<'PY' | while read -r src dst; do docker tag "$src" "$dst" && docker push "$dst"; done
import json, sys
reg = sys.argv[1]
for i in json.load(open("manifest.json"))["images"]:
    print(i["image"], f"{reg}/{i['image']}")
PY
cat <<MSG

Images pushed to $REGISTRY. Install with Helm (create the Secret first, e.g.
  kubectl create secret generic folio-secrets --from-literal=FOLIO_DATABASE_URL=... ):

  helm install folio helm/folio-$VERSION.tgz -n folio --create-namespace \\
    --set image.registry=$REGISTRY --set secrets.existingSecret=folio-secrets \\
    -f helm/values-offline.yaml
MSG
