# folio

A pipeline library plus a thin service that turns documents into a search index. Only the data contracts between stages are fixed. Building for a new customer takes three things: a **schema file + pipeline YAML + mapper function**. Requirements are in [PRD.md](PRD.md).

```
parse (PDF text layer + structure-analysis backend, OCR/VLM fallback) → chunk → map → embed → index (OpenSearch | Milvus | pgvector)
```

## Current status (2026-10-20)

- Implemented every FR in the PRD except DOCX/HWPX (FR-20, FR-21), which is deferred. Calling parse on a DOCX/HWPX file raises `NotImplementedError`.
- Verified: unit tests, sink integration tests (OpenSearch, Milvus, pgvector), Compose E2E, kind + Helm E2E (including upgrades and forced worker kills), and offline bundle install. Details: [wiki/2026-10-20-implementation-core-pipeline.md](wiki/2026-10-20-implementation-core-pipeline.md)
- Not settled: the default structure-analysis backend (Docling vs PP-StructureV3), pending a benchmark on customer samples. If `FOLIO_STRUCTURE_URL` is not set, the model-free `heuristic` backend is used.

## Install

```bash
pip install -e ".[all]"          # library + service + all sinks + S3
pip install -e ".[ocr]"          # OCR for the heuristic backend (rapidocr)
pip install -e ".[docling]"      # or ".[ppstructure]": in-process structure-analysis backend
pip install -e ".[dev]"          # tests
```

The library alone (`pip install -e .`) can call `run_pipeline` from a notebook.

## Quick start (local)

```bash
cp -r project-template myproject && cd myproject
export OPENSEARCH_URL=http://localhost:9200 FOLIO_EMBED_URL=http://localhost:8001/v1
folio embed-stub &                               # dev embedding server (replace with a real endpoint)
folio init                                       # storage, jobs table, index creation (idempotent)
folio run ./samples/ --pipeline contracts        # one JSON result line per file
folio schema apply [--reindex]                   # field added → mapping update; type change → new index + alias swap
folio reprocess --from map --index contracts     # reuses stored Document JSON/chunks, 0 model calls
folio eval testset --pipeline contracts --n 50 && folio eval run --pipeline contracts --testset testset.json
```

```python
from folio.contracts import load_config
from folio.pipeline import run_pipeline
result = run_pipeline("contract.pdf", load_config("pipelines/contracts.yaml"))
```

## Pipeline YAML

```yaml
parse: {fn: folio.parse:default, backend: auto, ocr_fallback: true, dpi: 144}
chunk: {fn: folio.chunk:auto, max_tokens: 512}
map:   mappers:map_contract               # if omitted, only common fields are filled
embed: {fn: folio.embed:http, batch_size: 64}
sink:  {type: opensearch, index: contracts}
storage: ${FOLIO_STORAGE_URI:-.folio/artifacts}
```

Any stage can be swapped for a `module:function`. The stage-function signatures and environment variables are in [docs/INTERFACES.md](docs/INTERFACES.md).

## Service

- `POST /v1/ingest` (multipart `file`, `pipeline`) → `202 {job_id}`
- `GET /v1/jobs/{id}` → `queued|running|succeeded|partial|failed` plus per-stage status and duration, and the error message
- `DELETE /v1/documents/{doc_id}?pipeline=`
- `/healthz`, `/readyz`

Run modes for the `folio` image: `folio api` / `folio worker` / `folio init` / `folio structure-server` / `folio embed-stub`.

## Deploy

- Docker Compose: `deploy/compose/` (`cp .env.example .env` → `docker compose up -d`)
- Kubernetes: `deploy/helm/folio/` (`helm install folio deploy/helm/folio -f values-dev.yaml`)
- Offline: `deploy/bundle/make_bundle.sh` → `install.sh`

See [deploy/README.md](deploy/README.md) for details.

## Tests

```bash
pytest -q                                              # unit tests (no models or external services)
pytest -q -m integration tests/integration/test_sinks_integration.py   # starts the OpenSearch/pgvector/Milvus containers
deploy/compose/e2e.sh                                  # Compose E2E
deploy/kind/e2e.sh                                     # kind + Helm E2E (including upgrades)
python scripts/bench_backends.py --backends heuristic,docling,ppstructure   # structure-backend benchmark
```

## Layout

```
folio/        contracts, parse, backends, chunk, embed, map, sinks, retrieval, eval, storage, pipeline, cli
service/      FastAPI + Celery
project-template/  schemas/ pipelines/ mappers.py dags/ samples/
deploy/       docker, compose, helm, bundle, kind
tests/        unit, integration, fixtures (generator: tests/fixtures/make_fixtures.py)
```
