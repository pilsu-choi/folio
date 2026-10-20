#!/usr/bin/env bash
# Build an offline bundle (FR-26): image tars, Helm chart package, compose files, optional model weights.
#   deploy/bundle/make_bundle.sh [--version 0.1.0] [--models DIR] [--out DIR] [--no-build] [--pull]
#     --models DIR  include model weights (copied to models/ in the bundle)
#     --no-build    fail instead of building images that are not present locally
#     --pull        docker pull third-party images (postgres, rabbitmq, minio) when missing locally
# Output: <out>/folio-bundle-<version>.tar.gz
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=0.1.0; MODELS=""; OUT="$root/deploy/bundle/out"; BUILD=1; PULL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION=$2; shift 2 ;;
    --models) MODELS=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    --pull) PULL=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
HELM=${HELM:-helm}
MINIO_TAG=RELEASE.2025-04-22T22-12-26Z
images=("folio:$VERSION" "folio-structure:$VERSION-cpu" "folio-opensearch:$VERSION"
        "postgres:16" "rabbitmq:3-management-alpine" "minio/minio:$MINIO_TAG")

have() { docker image inspect "$1" >/dev/null 2>&1; }
ensure() {
  have "$1" && return 0
  case "$1" in
    folio:*) [ $BUILD = 1 ] && docker build -f "$root/deploy/docker/Dockerfile" -t "$1" "$root" ;;
    folio-structure:*) [ $BUILD = 1 ] && docker build -f "$root/deploy/docker/Dockerfile.structure" \
        --build-arg BACKEND="${FOLIO_STRUCTURE_BACKEND:-heuristic}" --build-arg DEVICE=cpu -t "$1" "$root" ;;
    folio-opensearch:*) [ $BUILD = 1 ] && docker build -f "$root/deploy/docker/Dockerfile.opensearch" -t "$1" "$root" ;;
    *) [ $PULL = 1 ] && docker pull "$1" ;;
  esac
  have "$1" || { echo "image $1 not available (use build/--pull)" >&2; exit 1; }
}

name="folio-bundle-$VERSION"
work=$(mktemp -d); stage="$work/$name"
trap 'rm -rf "$work"' EXIT
mkdir -p "$stage/images" "$stage/compose" "$stage/helm" "$OUT"

for img in "${images[@]}"; do
  ensure "$img"
  f="$stage/images/$(echo "$img" | tr '/:' '__').tar"
  echo "saving $img" >&2
  docker save -o "$f" "$img"
done

"$HELM" package "$root/deploy/helm/folio" --version "$VERSION" --app-version "$VERSION" -d "$stage/helm" >/dev/null
cp "$root"/deploy/compose/docker-compose*.yml "$root/deploy/compose/.env.example" "$stage/compose/"
cp -r "$root/project-template" "$stage/project"
cp "$root/deploy/helm/folio/values-"*.yaml "$stage/helm/"
cp "$root/deploy/bundle/install.sh" "$stage/install.sh"; chmod +x "$stage/install.sh"
if [ -n "$MODELS" ]; then
  echo "copying models from $MODELS" >&2
  mkdir -p "$stage/models"; cp -r "$MODELS"/. "$stage/models/"
fi

python3 - "$stage" "$VERSION" "${images[@]}" <<'PY'
import hashlib, json, os, subprocess, sys
stage, version, *images = sys.argv[1:]
out = []
for img in images:
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", img]))[0]
    tar = os.path.join(stage, "images", img.replace("/", "_").replace(":", "_") + ".tar")
    h = hashlib.sha256()
    with open(tar, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    out.append({"image": img, "id": info["Id"], "repoDigests": info.get("RepoDigests", []),
                "imageSizeBytes": info["Size"], "tar": os.path.relpath(tar, stage),
                "tarBytes": os.path.getsize(tar), "tarSha256": h.hexdigest()})
json.dump({"name": "folio-bundle", "version": version, "images": out,
           "models": os.path.isdir(os.path.join(stage, "models")),
           "chart": sorted(os.listdir(os.path.join(stage, "helm")))}, open(os.path.join(stage, "manifest.json"), "w"), indent=2)
PY

tar -C "$work" -czf "$OUT/$name.tar.gz" "$name"
echo "bundle: $OUT/$name.tar.gz ($(du -h "$OUT/$name.tar.gz" | cut -f1))" >&2
