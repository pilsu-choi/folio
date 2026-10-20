---
type: concept
title: folio core pipeline implementation (all PRD items except DOCX/HWPX)
description: Implementation work, verification results, decisions, and remaining work for every PRD FR except FR-20/21
tags: [implementation, parse, sinks, service, deploy, helm, compose, e2e]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../README.md
  - ../docs/INTERFACES.md
  - ./2026-10-20-prd-container-deployment.md
---

# folio core pipeline implementation

Per user instruction, implemented [PRD.md](../PRD.md) with DOCX/HWPX (FR-20, FR-21) deferred.

## Work environment

- The repository was not a git repository, so I ran `git init` and created a local `dev` branch (no remote). Work happened in the `feat/core-pipeline` branch and the worktree `../folio-wt/core-pipeline`.
- Python 3.12 venv (uv). Sub-agents worked in parallel on four areas with separate file ownership: parse/backends, chunk·embed·sinks·retrieval·eval, pipeline·storage·CLI·service·template, and deploy. Contracts (`folio/contracts/`) and the internal interface ([docs/INTERFACES.md](../docs/INTERFACES.md)) were written first.

## Implementation

| Area | FR | Location |
| --- | --- | --- |
| Contracts (Document JSON, Chunk Record, Sink, YAML) + JSON Schema | contract spec | `folio/contracts/` |
| PDF text layer + structure-backend coordinate matching, quality check → OCR fallback, tables, rotation/CropBox, char matching, image input, VLM fallback (off by default) | FR-01–06 | `folio/parse/` |
| `StructureBackend` + heuristic/docling/ppstructure/http adapters, structure server, benchmark | FR-19 | `folio/backends/`, `scripts/bench_backends.py` |
| chunk auto/section/table/page/fixed, default mapper, HTTP embedding + dev stub | FR-07–09 | `folio/chunk/`, `folio/map.py`, `folio/embed/` |
| OpenSearch, Milvus, and pgvector sinks (replace semantics, alias swap, pgvector without DDL) | FR-10, 11 | `folio/sinks/` |
| `run_pipeline`, `reprocess`, local/S3 storage, CLI | FR-12, 13, 16 | `folio/pipeline.py`, `folio/storage.py`, `folio/cli.py` |
| Ingest API, jobs table, Celery worker | FR-14 | `service/` |
| Airflow example DAG | FR-15 | `project-template/dags/folio_ingest.py` |
| Hybrid search (RRF) + rerank, tenant_id/acl filters | FR-17 | `folio/retrieval/` |
| Testset, Recall@k/MRR/nDCG, per-commit comparison | FR-18 | `folio/eval/` |
| Images, Compose, Helm, external infra toggles, offline bundle | FR-22–26 | `deploy/` |

## Verification results (actually run)

- Unit tests: `pytest -q` → 102 passed (no models or external services).
- Sink integration: 13 passed, 6 skipped against OpenSearch 2.19.1, pgvector pg16, and Milvus v2.6.25 containers. Each skip is a backend-specific test that doesn't apply under another backend's parametrization. Re-ingest leaves the count unchanged, stale chunks are removed, the reindex alias is swapped, the pgvector table schema is unchanged, and other tenants' documents don't appear in results.
- Local CLI (OpenSearch + embedding stub):
  - Running `init` twice gives `created` → `unchanged`.
  - Running `run` twice leaves the count at 14.
  - Adding a field gives `mapping_updated`.
  - `reprocess --from map` reflects the mapper change on all 14 documents with 0 structure/OCR calls.
  - `--reindex` swaps the alias from `contracts-v1` to `-v2` and keeps the count at 14.
  - `eval testset/run/compare` runs.
- Compose E2E (`deploy/compose/e2e.sh`): scenario 3 passes.
  - Re-uploading the same file doesn't grow the count.
  - mixed.pdf goes through the structure server's OCR (rapidocr) and ends `succeeded`.
  - corrupt.pdf ends `failed` and its `ParseError` message can be retrieved.
- kind + Helm E2E (`deploy/kind/e2e.sh`): installs and passes scenario 3. Five jobs in flight survive `helm upgrade` (worker pod replaced) and all end `succeeded`.
  - The old worker finished them during warm shutdown.
  - In a separate run, I force-deleted the worker pod (`--grace-period=0 --force`) while 2 jobs were in `parse`. They were redelivered and all 4 jobs ended `succeeded` (acks_late + reject_on_worker_lost).
- Offline bundle: `make_bundle.sh` produced a 1.9 GB tar.gz. `install.sh` ran inside a Docker-in-Docker container with `--network none`: it loaded the images, brought Compose up, and an upload ended `succeeded`.
- Images: `folio:0.1.0` 654 MB and `folio-structure:0.1.0-cpu` (heuristic) 757 MB both run as uid 10001. `folio-opensearch:0.1.0` (nori included) is 1.46 GB.
- `helm lint` and `helm template` pass with the default, dev, gpu, and offline values. `docker compose config` passes.

### Structure-backend benchmark (fixtures, CPU)

| backend | digital text match | tables (rows/cols, cells, spans) | OCR similarity | sec/page |
| --- | --- | --- | --- | --- |
| heuristic | 1.000 | match | 0.72 (rapidocr) | 0.04 (0.31 with OCR) |
| docling 2.140 | 1.000 | match | 0.75 | 1.75 |
| ppstructure | 1.000 | match | 0.91 | 20.6 (includes model load) |

Only the synthetic fixtures were used (`tests/fixtures/`). The default backend has not been chosen, because customer samples with Korean text are needed.

Download record:

| Item | Size |
| --- | --- |
| `.venv-bench` (docling) | 1.7 GB |
| HF model cache | ~506 MB |
| `.venv-bench-paddle` | 1.6 GB |
| `~/.paddlex` models | 847 MB |

## Decisions and assumptions

- Decision: the coordinate system of the `StructureBackend.analyze` input/output is page-image pixels. parse converts it back to pt.
- Decision: `backend: auto` picks the HTTP structure server if `FOLIO_STRUCTURE_URL` is set, otherwise the model-free heuristic backend.
- Decision: `doc_id` for uploads via the API comes from `upload://{pipeline}/{filename}` plus the content hash. Re-uploading the same file is therefore idempotent.
- Decision: `minio/minio` no longer pulls from Docker Hub or quay. I replaced it with `cgr.dev/chainguard/minio` pinned by digest. The offline bundle verifies the digest and then saves the image by tag, because `docker load` drops digests.
- Decision: the Helm init step is a regular Job with a revision suffix, not a pre-install hook, because the bundled infra and ConfigMap have to exist first. External-infra deployments can choose `init.mode=hook`.
- Decision: worker concurrency is set by `FOLIO_WORKER_CONCURRENCY`, default 2.
- Assumption: the dev embedding is a hash-based stub (`folio embed-stub`, 384 dimensions). Production needs a real OpenAI-compatible endpoint such as TEI or vLLM.

## Not done or not verified

- DOCX/HWPX (FR-20, FR-21): deferred.
- Not verified:
  - The FR-15 Airflow DAG (no local Airflow run).
  - Real-image builds with the docling/ppstructure and GPU structure backends.
  - The Helm offline path through a private registry.
  - The FR-25 external-infra switch.
  - The CI workflow (`.github/workflows/ci.yml`).
  - Image vulnerability scanning.
  - The Korean nori analyzer itself; the integration tests used the standard analyzer.
- Known limitations:
  - Reading order is a simple y→x sort, so multi-column documents get interleaved.
  - The heuristic backend's table detection needs ruled lines.
  - rapidocr's default models are weak on Korean.
  - pgvector keyword search uses `simple` tsvector.
- ruff: some intentional broad `except` clauses (page-level isolation, readiness) remain.
