# folio Helm chart

Helm 3, no subchart dependencies (installs offline). `appVersion` 0.1.0.

```bash
helm lint deploy/helm/folio
helm install folio deploy/helm/folio -n folio --create-namespace -f deploy/helm/folio/values-dev.yaml
```

Value files: `values.yaml` (defaults, no secrets), `values-dev.yaml` (bundled infra + embed stub, dev-only passwords),
`values-gpu.yaml` (overlay: GPU docling, model PVC, nodeSelector/tolerations), `values-offline.yaml` (private registry).

## Secrets

No default secret values exist. Either
- `secrets.existingSecret: my-secret` (keys = env var names from docs/INTERFACES.md: `FOLIO_DATABASE_URL`, `FOLIO_BROKER_URL`,
  `PGVECTOR_DSN`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OPENSEARCH_USER/PASSWORD`, `MILVUS_TOKEN`, `FOLIO_EMBED_API_KEY`;
  plus `POSTGRES_PASSWORD`/`RABBITMQ_PASSWORD`/`MINIO_ROOT_PASSWORD` only when the matching bundled infra is on), or
- `secrets.create: true` + `secrets.values` (+ `infra.<x>.password` for bundled infra, from which URLs are derived).

## Infra toggles (FR-25)

`infra.postgres|rabbitmq|opensearch|minio|milvus.enabled` deploy simple single-replica StatefulSets (dev/PoC).
`infra.postgres.extraDatabases` adds databases/owners on the bundled PostgreSQL (e.g. Airflow metadata, see
[../../airflow/README.md](../../airflow/README.md)); enable `infra.postgres.persistence` when sharing it.
Disable them and fill `external.*` (OpenSearch URL, S3 endpoint, Milvus URI, structure/embed URLs) and the Secret
(`FOLIO_DATABASE_URL`, `FOLIO_BROKER_URL`, ...) to use existing services. Only values change; image and code stay the same.

## PostgreSQL backup (optional)

`backup.enabled: true` adds a CronJob (default `0 3 * * *`, `timeZone: Asia/Seoul`, keep 7) that runs `pg_dump -Fc` of
the bundled PostgreSQL's folio DB and every `infra.postgres.extraDatabases` entry into `<UTC timestamp>/<db>.dump`.
Bundled PostgreSQL only; an external DB keeps its own backups.

| `backup.target` | Where | Notes |
| --- | --- | --- |
| `pvc` (default) | `backup.pvc.existingClaim` (e.g. NAS/NFS-backed) or a chart PVC `<fullname>-backup` | chart PVC is kept on `helm uninstall`; prune touches only `<timestamp>` dirs, so a shared claim is safe |
| `s3` | `FOLIO_STORAGE_URI` bucket under `backup.s3.prefix` (`_backups/postgres`) | bundled MinIO = same cluster as the DB: no protection against losing the cluster |

The init Job writes a test file to the backup PVC at install/upgrade, so a WaitForFirstConsumer volume binds before
`helm --wait` gives up and permission problems (NFS `root_squash`, the pods run as uid/gid 10001) show up immediately.

Run one now: `kubectl -n folio create job --from=cronjob/folio-backup backup-manual`.

Restore (pvc target; for s3 download the dump first, e.g. with the api pod's `folio.storage`):

```bash
# pod with the backup PVC + psql (PGHOST/PGUSER/PGPASSWORD of the bundled postgres), then:
ls /backup                                                     # pick a <timestamp>
pg_restore -d airflow --clean --if-exists --exit-on-error /backup/<timestamp>/airflow.dump   # into the existing DB
createdb airflow_new && pg_restore -d airflow_new --exit-on-error /backup/<timestamp>/airflow.dump  # or a fresh one
```

Stop the consumers first (scale folio api/worker and Airflow to 0) so nothing writes during the restore. Airflow needs
the same fernet key as before to read restored connections/variables.

## Init Job ordering

`folio init` + `folio schema apply` run in one Job (`init.mode`):

- `job` (default): a regular Job named `<release>-init-<revision>`. A `pre-install` hook would run before the bundled
  infra, the ConfigMap, the Secret and the ServiceAccount exist, and a `pre-upgrade` hook would still see the OLD
  ConfigMap. A per-revision regular Job runs after the objects are applied (so it sees the new project config), and
  initContainers wait for the bundled infra ports. `helm --wait` waits for it. Cost: on upgrade it runs alongside the
  rollout rather than before it (schema apply is idempotent/additive; use `init.reindex` deliberately).
- `hook`: Helm `pre-install,pre-upgrade` hook (hook-delete-policy `before-hook-creation`). Requires external infra that
  already exists; the chart's ServiceAccount, ConfigMap and Secret are then also rendered as pre-install hooks (weight -10)
  so the hook Job can mount them. Trade-off: those three objects are not removed by `helm uninstall`. Rejected at
  render time when bundled infra is enabled.

## Rolling upgrades

Worker pods get `terminationGracePeriodSeconds` (default 300) and rolling update `maxUnavailable: 0`; SIGTERM triggers Celery
warm shutdown so in-flight tasks finish. Set it above your longest task. Config changes (ConfigMap) roll api/worker via a checksum annotation.

## Project config

`project.fromImage: true` uses the project baked into the image. Otherwise set `project.pipelines`, `project.schemas.opensearch|milvus`
(filename -> content) and optionally `project.mappers`; they are mounted read-only at `/app/project`.

## GPU and models

`structure.gpu.enabled` adds `limits."nvidia.com/gpu"` and switches the default image tag to `<appVersion>-gpu`.
Model weights mount at `/models` from `structure.models` (`emptyDir`, `pvc` (created or `existingClaim`), `hostPath`).

## Document conversion

`convert.enabled` (default) runs `folio-convert` (`<fullname>-convert:9100`) for DOCX/HWPX/HWP -> PDF; api/worker get
`FOLIO_CONVERT_URL`. `convert.workers` bounds concurrent conversions per pod (keep it at or below the CPU limit); scale with
`convert.replicas`. Customer-licensed fonts: `convert.fonts.existingClaim` mounts a PVC read-only at
`/usr/share/fonts/custom`; otherwise open fonts substitute them. External server: `convert.enabled=false` +
`external.convert.url`.

## Private registry

`image.registry: reg.local:5000` prefixes every image (including bundled infra); add `imagePullSecrets` when needed.
