# folio deployment

One image contract, two runtimes. The Python code does not know whether it runs in Compose or Kubernetes; the
environment variables are listed in [docs/INTERFACES.md](../docs/INTERFACES.md).

| Path | Purpose |
| --- | --- |
| `docker/` | `Dockerfile` (folio: api/worker/cli), `Dockerfile.structure` (model server), `Dockerfile.opensearch` (nori baked in), `Dockerfile.project` (customer image) |
| `compose/` | `docker-compose.yml` (profiles gpu/cpu/dev) + `.offline.yml` override, `.env.example`, `e2e.sh` |
| `helm/folio/` | Helm 3 chart (no subcharts, works offline), see its README |
| `bundle/` | `make_bundle.sh` / `install.sh` - air-gapped bundle (FR-26) |
| `kind/` | kind config and Helm E2E (`e2e.sh`) |

## Images

```bash
docker build -f deploy/docker/Dockerfile -t folio:0.1.0 .
# 구조 분석 서버: 기본은 PP-StructureV3 GPU, GPU 없는 환경은 cpu, 개발·CI는 heuristic
docker build -f deploy/docker/Dockerfile.structure --build-arg BACKEND=ppstructure --build-arg DEVICE=gpu -t folio-structure:0.1.0-gpu .
docker build -f deploy/docker/Dockerfile.structure --build-arg BACKEND=ppstructure --build-arg DEVICE=cpu -t folio-structure:0.1.0-cpu .
docker build -f deploy/docker/Dockerfile.structure --build-arg BACKEND=heuristic --build-arg DEVICE=cpu -t folio-structure:0.1.0-heuristic .
docker build -f deploy/docker/Dockerfile.opensearch -t folio-opensearch:0.1.0 .
# customer image
docker build -f deploy/docker/Dockerfile.project --build-arg FOLIO_IMAGE=folio:0.1.0 --build-arg PROJECT_DIR=my-project -t acme/folio:1 .
```

All run as uid 10001, hold no secrets, and model weights are mounted at `/models` (never baked in).

## Compose

```bash
cd deploy/compose && cp .env.example .env   # set the three CHANGE_ME passwords
docker compose up -d --build                # profiles from COMPOSE_PROFILES: infra + one of gpu (default) | cpu | dev
curl localhost:8080/readyz
```

- Compose refuses to start when `POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD` or `MINIO_ROOT_PASSWORD` is unset.
- External infra (FR-25): remove `infra` from `COMPOSE_PROFILES`, set `FOLIO_DATABASE_URL`, `FOLIO_BROKER_URL`,
  `OPENSEARCH_URL`, `S3_ENDPOINT_URL`, `AWS_*` ... in `.env` (the three passwords may then be dummy values).
- 구조 분석 서버: `gpu` 프로필(기본, PP-StructureV3, NVIDIA container toolkit 필요), GPU가 없으면 `cpu`, 개발·CI는 `dev`(heuristic). 모델 가중치는 `models` 볼륨(`FOLIO_MODELS_DIR`)에 두며, `/readyz`는 가중치 로드 후에 통과한다.
- Real embedding endpoint: set `FOLIO_EMBED_URL`/`FOLIO_EMBED_MODEL`; the stub can be stopped.
- Offline: `-f docker-compose.yml -f docker-compose.offline.yml` (no build, `pull_policy: never`).
- Worker `stop_grace_period` is 120s so SIGTERM (Celery warm shutdown) finishes the running task.
- OpenSearch security is disabled in the bundled service: dev / closed network only.
- Airflow example (FR-15): `--profile airflow` adds `airflow standalone` (UI on `${AIRFLOW_PORT:-8081}`, SQLite, DAGs unpaused at creation, dev only)
  with `project-template/dags/folio_ingest.py`. The DAG runs `folio run` in a sibling folio container via the
  mounted Docker socket (DockerOperator), so Airflow has no folio dependencies. Set `DOCKER_GID` (gid of
  `/var/run/docker.sock`) and `FOLIO_DOCKER_NETWORK=<project>_default`; the spawned container uses the project baked
  into `FOLIO_IMAGE` (build a customer image for your own pipelines). Socket access is root-equivalent on the host.
  Kubernetes: `FOLIO_AIRFLOW_RUNNER=kubernetes` switches to KubernetesPodOperator with env from the chart's
  `<fullname>-env` ConfigMap and Secret (`FOLIO_K8S_FULLNAME`, `FOLIO_K8S_NAMESPACE`, `FOLIO_K8S_SECRET`).

## Kubernetes

```bash
helm install folio deploy/helm/folio -n folio --create-namespace -f deploy/helm/folio/values-dev.yaml   # dev, all bundled
```

Production: external endpoints + `secrets.existingSecret`; see [helm/folio/README.md](helm/folio/README.md).

## Offline bundle

```bash
deploy/bundle/make_bundle.sh --version 0.1.0 [--models /path/to/weights]   # -> deploy/bundle/out/folio-bundle-0.1.0.tar.gz
# on the air-gapped host:
tar xzf folio-bundle-0.1.0.tar.gz && cd folio-bundle-0.1.0
./install.sh                          # Compose
./install.sh --registry reg.local:5000   # push to private registry, prints the helm command
```

## E2E

`deploy/compose/e2e.sh` and `deploy/kind/e2e.sh` upload `tests/fixtures/digital.pdf` via `POST /v1/ingest` and poll
`GET /v1/jobs/{id}` to `succeeded`; the kind script then runs `helm upgrade` with 5 jobs in flight and checks they all finish.
`AIRFLOW=1 deploy/compose/e2e.sh` additionally uploads the PDF to S3, triggers `folio_ingest` (scheduler + LocalExecutor),
waits for `success` and checks the chunks reached OpenSearch. CI: `.github/workflows/ci.yml`.
