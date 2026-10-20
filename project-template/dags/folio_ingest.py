"""Example Airflow DAG (FR-15): ingest a document with folio's `run_pipeline` via PythonOperator.

Trigger with conf: {"uri": "s3://bucket/key.pdf", "pipeline": "contracts"}
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator


def ingest(**context):
    from folio.pipeline import load_pipeline, run_pipeline

    conf = context["dag_run"].conf or {}
    cfg = load_pipeline(conf.get("pipeline", "contracts"))  # FOLIO_CONFIG_DIR -> project dir
    result = run_pipeline(conf["uri"], cfg, source_uri=conf.get("source_uri"))
    if result.status == "failed":
        raise RuntimeError(result.errors)
    return result.to_dict()


with DAG("folio_ingest", start_date=datetime(2026, 1, 1), schedule=None, catchup=False, tags=["folio"]) as dag:
    PythonOperator(task_id="run_pipeline", python_callable=ingest)
