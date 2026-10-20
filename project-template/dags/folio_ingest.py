"""Example Airflow DAG (FR-15): ingest one document by running `folio run` in the folio image.

Airflow only schedules; the work runs in a separate folio container, so Airflow needs no folio dependencies.
  FOLIO_AIRFLOW_RUNNER=docker      DockerOperator on the host Docker daemon (Compose `airflow` profile, default)
  FOLIO_AIRFLOW_RUNNER=kubernetes  KubernetesPodOperator; pod env from the Helm chart's `<fullname>-env` ConfigMap
                                   and Secret. The image must contain the project (deploy/docker/Dockerfile.project).

Trigger with conf: {"uri": "s3://bucket/key.pdf", "pipeline": "contracts", "source_uri": ""}
The task fails when `folio run` exits 1 (document failed); `partial` succeeds. Result JSON = last task log line
(docker runner: also the XCom). Production Airflow setup: deploy/airflow/README.md.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.sdk import DAG, Param

IMAGE = os.environ.get("FOLIO_IMAGE", "folio:0.1.0")
RUNNER = os.environ.get("FOLIO_AIRFLOW_RUNNER", "docker")
# folio container env (docs/INTERFACES.md) forwarded from the Airflow environment when set (docker runner).
FORWARD_ENV = (
    "FOLIO_LOG_LEVEL", "PGVECTOR_DSN", "OPENSEARCH_URL", "OPENSEARCH_USER", "OPENSEARCH_PASSWORD",
    "MILVUS_URI", "MILVUS_TOKEN", "S3_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION",
    "FOLIO_STORAGE_URI", "FOLIO_STRUCTURE_URL", "FOLIO_CONVERT_URL", "FOLIO_CONVERT_TIMEOUT", "FOLIO_EMBED_URL", "FOLIO_EMBED_MODEL", "FOLIO_EMBED_API_KEY",
    "FOLIO_VLM_URL", "FOLIO_VLM_MODEL", "FOLIO_RERANK_URL",
)
ARGS = ["run", "{{ params.uri }}", "--pipeline", "{{ params.pipeline }}", "--source-uri", "{{ params.source_uri }}"]

with DAG(
    "folio_ingest",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["folio"],
    params={
        "uri": Param(type="string", minLength=1, description="document URI: s3://bucket/key or a path inside the image"),
        "pipeline": Param("contracts", type="string"),
        "source_uri": Param("", type="string", description="original location recorded in the manifest"),
    },
) as dag:
    if RUNNER == "kubernetes":
        from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
        from kubernetes.client import models as k8s

        fullname = os.environ.get("FOLIO_K8S_FULLNAME", "folio")  # Helm `folio.fullname`
        KubernetesPodOperator(
            task_id="run_pipeline",
            namespace=os.environ.get("FOLIO_K8S_NAMESPACE", "folio"),
            image=IMAGE,
            arguments=ARGS,
            env_from=[
                k8s.V1EnvFromSource(config_map_ref=k8s.V1ConfigMapEnvSource(name=f"{fullname}-env")),
                k8s.V1EnvFromSource(
                    secret_ref=k8s.V1SecretEnvSource(name=os.environ.get("FOLIO_K8S_SECRET", fullname))
                ),
            ],
            security_context=k8s.V1PodSecurityContext(run_as_non_root=True, run_as_user=10001, run_as_group=10001),
            on_finish_action="delete_succeeded_pod",
            get_logs=True,
        )
    else:
        from airflow.providers.docker.operators.docker import DockerOperator

        DockerOperator(
            task_id="run_pipeline",
            image=IMAGE,
            command=ARGS,
            private_environment={k: os.environ[k] for k in FORWARD_ENV if os.environ.get(k)},
            network_mode=os.environ.get("FOLIO_DOCKER_NETWORK", "bridge"),
            mount_tmp_dir=False,  # Airflow itself runs in a container: its tmp path does not exist on the host
            auto_remove="force",  # logs are already in the task log
        )
