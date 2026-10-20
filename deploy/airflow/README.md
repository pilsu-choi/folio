# Production Airflow for folio (FR-15)

folio does not ship Airflow. The Compose `airflow` profile is a dev-only sandbox. In production:

| Situation | What to do |
| --- | --- |
| Airflow already runs on the same Kubernetes cluster | Deploy `dags/folio_ingest.py` with the env below + apply `rbac-folio-namespace.yaml` |
| Airflow runs elsewhere (VM, Docker) | Use the docker runner, or KubernetesPodOperator with a kubeconfig / `kubernetes_default` connection |
| No Airflow yet | Install the official Apache Airflow chart with `values-official-chart.yaml` (this folder) |

In every case the DAG only needs the Airflow image's bundled providers (`cncf.kubernetes` / `docker`); folio itself runs
in its own image, so nothing from folio is installed into Airflow.

## Install with the official chart

Tested 2026-10-20: chart `apache-airflow/airflow` 1.22.0 (Airflow 3.2.2) installed with these values on single-node k3s
(aarch64); git-sync -> scheduler -> KubernetesExecutor -> KubernetesPodOperator ran `folio_ingest` and indexed the
document (in-cluster auth + the RBAC below). Airflow in namespace `airflow`, folio release
`folio` in namespace `folio` (installed with `deploy/helm/folio`; it creates the `folio-env` ConfigMap and the Secret).

```bash
# 1. secrets (names referenced by values-official-chart.yaml)
kubectl create ns airflow
kubectl -n airflow create secret generic airflow-metadata \
  --from-literal=connection='postgresql+psycopg2://airflow:CHANGE_ME@db.example:5432/airflow'
kubectl -n airflow create secret generic airflow-api-secret-key --from-literal=api-secret-key="$(openssl rand -hex 32)"
kubectl -n airflow create secret generic airflow-jwt-secret --from-literal=jwt-secret="$(openssl rand -hex 32)"
# fernet key encrypts connections/variables in the DB: keep a copy, a reinstall with a new key cannot read them
kubectl -n airflow create secret generic airflow-fernet-key \
  --from-literal=fernet-key="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"

# 2. Airflow (edit the CHANGE_ME values first: folio image, git repo of the DAG)
helm repo add apache-airflow https://airflow.apache.org
helm install airflow apache-airflow/airflow --version 1.22.0 -n airflow -f deploy/airflow/values-official-chart.yaml

# 3. allow the Airflow worker SA to start pods in the folio namespace
kubectl apply -n folio -f deploy/airflow/rbac-folio-namespace.yaml
```

New DAGs are paused at creation (chart default): unpause `folio_ingest` in the UI or with `airflow dags unpause
folio_ingest`, then trigger it with `{"uri": "s3://bucket/key.pdf", "pipeline": "contracts", "source_uri": ""}`.

## Share folio's PostgreSQL (no external DB)

The folio chart's bundled PostgreSQL can host the Airflow metadata DB as a separate database and owner, created
idempotently by the folio init Job. Requirements: `infra.postgres.persistence.enabled=true` (the default emptyDir loses
data on restart) and a backup: `backup.enabled=true` in the folio chart dumps the folio and airflow databases daily
(PVC/NAS or S3, see [../helm/folio/README.md](../helm/folio/README.md#postgresql-backup-optional)). Single replica: a
postgres outage stops folio and Airflow.

```yaml
# folio release values (namespace folio)
infra:
  postgres:
    enabled: true
    persistence: {enabled: true, size: 10Gi}
    extraDatabases:
      - {name: airflow, user: airflow, password: CHANGE_ME}   # with secrets.existingSecret: key POSTGRES_AIRFLOW_PASSWORD
```

```bash
kubectl -n airflow create secret generic airflow-metadata \
  --from-literal=connection='postgresql+psycopg2://airflow:CHANGE_ME@folio-postgres.folio.svc.cluster.local:5432/airflow'
```

Re-running the init Job (`helm upgrade`) keeps existing data and re-applies the password. `airflow` owns only its own
database; it cannot write to the `folio` database.

## Env read by the DAG

| Variable | Default | Meaning |
| --- | --- | --- |
| `FOLIO_AIRFLOW_RUNNER` | `docker` | `kubernetes` = KubernetesPodOperator |
| `FOLIO_IMAGE` | `folio:0.1.0` | image with the project baked in (`deploy/docker/Dockerfile.project`) |
| `FOLIO_K8S_NAMESPACE` | `folio` | namespace of the folio release; pods use its short service names |
| `FOLIO_K8S_FULLNAME` | `folio` | folio chart fullname: env from ConfigMap `<fullname>-env` |
| `FOLIO_K8S_SECRET` | fullname | Secret with connection secrets (`secrets.existingSecret` if used) |
| `FOLIO_DOCKER_NETWORK` | `bridge` | docker runner only |

## Notes

- Metadata DB: the Airflow chart's bundled PostgreSQL subchart (`bitnamilegacy/postgresql`) is for evaluation only;
  use an external PostgreSQL or folio's bundled one (above) via `data.metadataSecretName`.
- RBAC: the chart's worker SA (`<release>-worker`) runs the task with KubernetesExecutor. The Role grants pods
  create/get/list/watch/delete/patch, pods/log get and events list/watch in the folio namespace only.
- `helm install --wait` / Argo CD: the values run DB migration and user creation as plain Jobs
  (`useHelmHooks: false`); with the chart's default post-install hooks the install deadlocks (pods wait for the
  migration, the hook waits for the pods).
- Task logs: with KubernetesExecutor the log lives on the worker pod, which is deleted after the task. Configure
  remote logging (e.g. `config.logging.remote_logging` to S3) or `logs.persistence` (RWX PVC) to keep them; the
  example values leave this to the site (not tested).
- Failed folio pods are kept for debugging (`on_finish_action="delete_succeeded_pod"`); successful ones are deleted.
- Task result: with the kubernetes runner the result JSON is in the task log (no XCom); with the docker runner the
  last log line is also pushed as XCom.
- Air-gapped: mirror `apache/airflow:<tag>` and the git-sync image to the private registry
  (`images.airflow.repository`, `images.gitSync.repository`), or bake DAGs into a custom Airflow image instead of
  git-sync. These images are not part of the folio offline bundle.
