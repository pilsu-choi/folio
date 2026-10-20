# Internal interfaces (cross-module contract)

The public contracts are in `folio/contracts/` (do not change them without coordination).
This file fixes the internal call signatures modules use between each other.

## Stage functions

```python
folio.parse.default(uri: str, **opts) -> folio.contracts.Document
folio.chunk.adaptive(doc: Document, **opts) -> list[dict]      # also page/fixed
folio.map.common(doc: Document, chunk: dict) -> dict            # identity: default when `map` omitted
folio.embed.http(texts: list[str], **opts) -> list[list[float]]
```

- `uri`: local path, `file://...`, or `s3://bucket/key` (parse reads bytes via `folio.storage.read_uri(uri) -> bytes`).
- `parse.default` opts: `backend` (`"auto"` | `"module:Class"` | `{fn: "module:Class", **kwargs}`), `ocr_fallback=True`,
  `dpi=144`, `char_match=False`, text-layer quality thresholds. `auto` = `folio.backends.http:HttpBackend`
  when `FOLIO_STRUCTURE_URL` is set, otherwise `folio.backends.heuristic:HeuristicBackend`.
  `vlm="auto"` (default): VLM enabled iff `FOLIO_VLM_URL` is set; or `{url, model, api_key, timeout}` | `"module:function"` |
  callable(png[, task]) -> str; `False`/`None`/`"off"` disables (`meta.vlm = {"enabled": false, "reason": ...}`).
  Enabled: image files and low-quality PDF pages -> VLM markdown (`text_source=vlm`, bbox = page box); figures and weak
  structure tables -> VLM (`figure_min_area=0.01`, `table_min_coverage=0.8`, `table_max_empty=0.6`). Disabled/failed ->
  structure-backend OCR; failures are listed in `meta.vlm_fallbacks = [{page, task, error}]`.
  Formats by MIME/extension: PDF, PNG/JPG/TIFF/BMP/WebP, PPTX (`pptx_mode="native"|"convert"`, `pptx_notes=False`),
  XLSX (`xlsx_include_hidden=False`), Markdown, TXT (encoding auto-detect, `meta.encoding`), DOCX/HWPX/HWP (FR-20).
  DOCX/HWPX/HWP: `docx_mode`/`hwpx_mode="convert"` (default; `hwpx_mode` covers HWP; `"native"` = FR-21, NotImplementedError)
  converts to PDF and takes the PDF path; `pptx_mode="convert"` uses the same converter. `converter="auto"`:
  `FOLIO_CONVERT_URL` server, else local (`soffice`, `rhwp`); `"local"` | `{url, timeout}` | `"module:function"` |
  callable(data, mime) -> PDF bytes or (bytes, info). `convert_timeout` (s; env `FOLIO_CONVERT_TIMEOUT`, default 300).
  `doc.meta["convert"] = {converter, version, ms, pdf_hash[, server, errors]}`; pages/bbox refer to the PDF made in that
  parse (LibreOffice output is not byte-reproducible, so `pdf_hash` identifies, not rebuilds, it) while
  `mime_type`/`content_hash`/`doc_id` come from the original file. Failure -> `folio.parse.convert.ConvertError`.
  Dev VLM server: `folio.backends.vlm_stub:serve(host, port=8002)` (OpenAI-compatible, deterministic).
- parse records model-call counts in `doc.meta["calls"] = {"structure": n, "ocr": n, "vlm": n}`
  and per-stage timings in `doc.meta["timings"]`; page failures in `doc.meta["errors"] = [{"page": n, "error": str}]`.
- chunk opts: `max_tokens=1024` (bounds `contextualized_content`), `min_tokens=128`, `overlap=0` (only `fixed` and forced
  fixed splits), `tokenizer="module:function"` (text -> int; default model-free `count_tokens`), `include_header_footer=False`;
  unknown opts raise `ConfigError`. Records satisfy the Chunk Record (validated) plus `pages`, `chunk_type`, `bbox`,
  `contextualized_content`, `chunk_strategy`; `chunk_id = make_chunk_id(doc_id, block_range, settings_hash(strategy, opts))`
  with `block_range` like `p1b0-p1b4` or `p2b3#r10-19` (split block: `s` sentences, `i` list items, `r` rows, `l` lines).
- embed opts: `url` (OpenAI-compatible base, `.../v1`; default env `FOLIO_EMBED_URL`), `model` (env `FOLIO_EMBED_MODEL`),
  `batch_size=64`, `api_key` (env `FOLIO_EMBED_API_KEY`), `timeout=60`.

## Sinks

```python
folio.sinks.create_sink(spec: dict, *, config_dir: str | None = None) -> Sink
```

`spec` is the pipeline YAML `sink` mapping: `{type: opensearch|milvus|pgvector, ...}` or `{fn: "module:factory", ...}`.
Built-in sinks implement `upsert`, `delete` and these optional methods (pipeline/CLI check with `hasattr`):

- `expected_dimension() -> int | None` — vector dim from schema/table; pipeline fails before upsert on mismatch.
- `apply_schema(reindex: bool = False) -> dict` — create if missing / add fields / reindex + alias swap. Idempotent.
- `ping() -> bool` — readiness.
- `count(doc_id: str | None = None) -> int` — for tests.

Schema files: `{config_dir}/schemas/opensearch/{index}.json`, `{config_dir}/schemas/milvus/{collection}.json`.
pgvector never runs DDL.

## Retrieval

```python
folio.pipeline.retriever(cfg) -> Retriever          # cached; cfg.sink + cfg.embed (same model as ingest) + cfg.retrieval
folio.pipeline.search(cfg, query, k=None, filters=None, mode=None) -> list[Hit]   # + required_filters check
folio.retrieval.create_retriever(sink_spec, config_dir=None, embed=None, *, retrieval=None, sink=None) -> Retriever
Retriever.search(query, k=None, filters=None, mode=None) -> list[Hit]   # Hit(chunk_id, doc_id, score, content, record)
```

- `retrieval:` (`RetrievalConfig`, `extra=forbid`): `mode` (`hybrid|bm25|vector`, default hybrid), `k` (10), `fields`
  (BM25 field -> weight, default `{content: 1}`; only OpenSearch accepts other fields, Milvus/pgvector raise `ConfigError`),
  `rrf_k` (60), `candidates` (per-leg, default `max(k*3, 30)`), `rerank` (step `fn(query, hits, **opts) -> hits`, sees
  the candidates before the cut to `k`), `required_filters` (list of filter names), `fn` (custom
  `factory(sink, embed, **settings) -> object with search(...)`; required for a custom `sink.fn`).
- `filters`: `{field: value}`; scalar = equality, list = any element, dict = range over `gt|gte|lt|lte` (number or string
  bounds). `None` values are dropped. Field names must be identifiers; violations raise `ValueError`.
  pgvector: range on a real column compares the column, on `metadata` casts to numeric for number bounds, text otherwise;
  range on an array column is a `ConfigError`.
- Service: `POST /v1/search` maps `ValueError`/`ConfigError` to 400; `k` is capped at 100.

## Generation

```python
folio.generation.answer(cfg, messages, k=None, filters=None, mode=None) -> Answer   # Answer(answer, query, citations, hits)
folio.generation.http(messages, *, url, model, api_key, temperature=0, max_tokens=1024, timeout=120) -> str
```

- `generation:` (`GenerationConfig`, `extra=forbid`, default absent = off; `true` = defaults): `chat` (step
  `fn(messages, **opts) -> str`, default `folio.generation:http`, env `FOLIO_LLM_URL`/`FOLIO_LLM_MODEL`/`FOLIO_LLM_API_KEY`),
  `system` (instruction override; numbered passages are appended), `rewrite` (true), `max_context_tokens` (6000; top hit
  always kept), `max_history_tokens` (2000; oldest turns dropped, kept history starts with a user turn).
- Stateless multi-turn: `messages` is the whole conversation, roles `user`/`assistant`, last one `user`. With history
  and `rewrite`, one chat call rewrites the last question into a standalone `query`; the first turn skips it. Search
  uses `folio.pipeline.search` (same `retrieval:` and `required_filters`). `[n]` markers are stripped from past assistant
  turns. `citations` = `[n]` in the reply within `1..len(hits)`, each `{n, chunk_id, doc_id}` of `hits[n-1]`.
- Errors: no `generation:` -> `ConfigError`, bad messages/filters -> `ValueError`, chat step failure -> `GenerationError`.
- Service: `POST /v1/chat {messages (1..50, content <= 20000 chars), pipeline, k, mode, filters}` ->
  `{pipeline, answer, query, citations, hits}`; `system` role or empty messages 422, `ValueError`/`ConfigError` 400,
  `GenerationError` 502. No streaming yet.
- Claude chat step: `folio.generation.anthropic:chat(messages, *, model="claude-opus-5-5", max_tokens=16000, effort=None,
  fallbacks=True, ...) -> str` (extra `folio[anthropic]`, credentials from `ANTHROPIC_API_KEY` etc., model override
  `FOLIO_LLM_MODEL`). `system` turns become the top-level `system`; `fallbacks` sends `fallbacks="default"` (Claude API
  only); a refusal raises.

## Chat app (`apps/chat`, extra `folio[app]`)

`uvicorn --factory apps.chat.main:create_app`. It talks to folio only over HTTP (`POST /v1/chat`) and imports nothing
from `folio`. Login (OIDC code flow + PKCE via Authlib, or a fixed `dev` user) sets a signed session cookie
(`folio_chat`, HttpOnly, SameSite=Lax, 12 h) with `{sub, email, name, acl}`.

| Method · path | Body → response |
| --- | --- |
| `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout` (204) | OIDC redirect · callback · local logout |
| `GET /api/me` | `{sub, email, name, auth_mode, pipelines, acl_keys}` |
| `GET/POST /api/conversations` | list (newest first) · `{title?, pipeline?}` → 201 conversation |
| `GET/PATCH/DELETE /api/conversations/{id}` | conversation + `messages` · `{title}` · 204 |
| `GET /api/conversations/{id}/messages` | messages in order |
| `POST /api/conversations/{id}/messages` | `{content, k?, mode?, filters?}` → 201 `{messages: [user, assistant], conversation}` |
| `PATCH /api/messages/{id}` | `{feedback: up\|down\|null}` (assistant messages only) |
| `DELETE /api/conversations/{id}/messages/{mid}` | 204, deletes `mid` and every later message |

- Every query is scoped by the login `sub`; another user's ids return 404.
- Send: history is rebuilt from the database (newest 50, starting with a user turn), `filters = {**client, **acl}`
  (login ACL wins), and the conversation's `pipeline` is used. The turn is stored only when folio answered: folio
  400/422 -> 400, 502 -> 502, other errors or unreachable -> 503. The first question becomes the title.
- `acl`: `FOLIO_APP_ACL_CLAIMS` maps filter fields to claims. A user missing a mapped claim gets 403 on send, because
  folio drops `None` filters, which would widen the search.

## Storage

```python
folio.storage.open_storage(uri: str) -> Storage   # local path / file:// / s3://bucket/prefix (env S3_ENDPOINT_URL)
folio.storage.read_uri(uri: str) -> bytes
Storage.put_bytes(key, data) / get_bytes(key) / put_json(key, obj) / get_json(key) / exists(key) / list(prefix) / delete(prefix)
```

Artifact layout: `{index}/{doc_id}/source{ext}`, `{index}/{doc_id}/document.json`, `{index}/{doc_id}/chunks.json`
(chunk output before map), `{index}/{doc_id}/manifest.json` (`source_uri`, `mime_type`, `pipeline`).

## Pipeline

```python
folio.pipeline.run_pipeline(uri, cfg: PipelineConfig, stop_after: str | None = None) -> PipelineResult
folio.pipeline.reprocess(cfg, start_from: "parse"|"chunk"|"map", doc_ids: list[str] | None = None) -> list[PipelineResult]
```

Stages: `parse → chunk → map → embed → index`. `PipelineResult` has `doc_id`, `status` (`succeeded|partial|failed`),
`timings`, `counts`, `errors`.

## Environment variables (same for Compose and Helm)

| var | meaning |
| --- | --- |
| `FOLIO_CONFIG_DIR` | project dir with `pipelines/`, `schemas/`, `mappers.py` (default `/app/project` in image, cwd locally) |
| `FOLIO_STORAGE_URI` | overrides pipeline `storage` |
| `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | S3-compatible storage |
| `FOLIO_DATABASE_URL` | jobs DB (SQLAlchemy URL, `postgresql+psycopg://...`; sqlite allowed for tests) |
| `FOLIO_BROKER_URL` | Celery broker (`amqp://...`) |
| `OPENSEARCH_URL`, `OPENSEARCH_USER`, `OPENSEARCH_PASSWORD` | OpenSearch |
| `MILVUS_URI`, `MILVUS_TOKEN` | Milvus |
| `PGVECTOR_DSN` | pgvector sink DB (App DB) |
| `FOLIO_STRUCTURE_URL` | structure model server base URL |
| `FOLIO_CONVERT_URL`, `FOLIO_CONVERT_TIMEOUT` | DOCX/HWPX/HWP -> PDF convert server base URL (unset = local binaries), timeout (s) |
| `FOLIO_CONVERT_WORKERS`, `FOLIO_CONVERT_FONT_PATH` | convert server only: concurrent conversions, rhwp `--font-path` |
| `FOLIO_VLM_URL`, `FOLIO_VLM_MODEL` | VLM for image content (OpenAI-compatible chat); unset = VLM off (`vlm="auto"`) |
| `FOLIO_EMBED_URL`, `FOLIO_EMBED_MODEL`, `FOLIO_EMBED_API_KEY` | embedding endpoint |
| `FOLIO_RERANK_URL` | optional rerank endpoint |
| `FOLIO_LLM_URL`, `FOLIO_LLM_MODEL`, `FOLIO_LLM_API_KEY` | chat LLM for `generation:` / `POST /v1/chat` and `eval testset --method llm` (OpenAI-compatible; testset falls back to `FOLIO_VLM_URL`/`FOLIO_VLM_MODEL`) |
| `FOLIO_LOG_LEVEL` | logging level |

Chat app (`apps/chat`):

| Variable | Meaning |
| --- | --- |
| `FOLIO_APP_AUTH` | `oidc` (default; startup fails without the secret and client) or `dev` (no login: fixed user `FOLIO_APP_DEV_USER`, claims `FOLIO_APP_DEV_CLAIMS` JSON) |
| `OIDC_ISSUER` (or `OIDC_METADATA_URL`), `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES` | IdP (Keycloak, Entra ID, Google, ...); redirect URI `<app>/auth/callback` |
| `FOLIO_APP_SECRET_KEY` | session cookie signing key (required for `oidc`) |
| `FOLIO_APP_COOKIE_SECURE` | `Secure` cookie flag (default true for `oidc`) |
| `FOLIO_APP_PUBLIC_URL` | external base URL behind a proxy (builds the redirect URI) |
| `FOLIO_APP_ACL_CLAIMS` | JSON `{filter_field: claim}`, e.g. `{"tenant_id": "tenant"}` |
| `FOLIO_APP_PIPELINES` | comma list users may pick; empty = folio's default pipeline |
| `FOLIO_APP_DATABASE_URL` | conversations DB (default `sqlite:///.folio/app.db`; PostgreSQL for production) |
| `FOLIO_API_URL` | folio API base (default `http://localhost:8080`) |

## Commands (image entrypoint is `folio`)

`folio api` (FastAPI on :8080: ingest, jobs, search, chat), `folio worker` (Celery), `folio init`, `folio schema apply [--reindex]`,
`folio run [--source-uri]`, `folio reprocess`, `folio eval ...`, `folio structure-server` (structure backend HTTP server on :9000),
`folio convert-server` (`POST /v1/convert`, multipart `file` + form `mime` -> PDF, headers `x-folio-converter[-version]`;
:9100, image `folio-convert`),
`folio embed-stub` (deterministic OpenAI-compatible embedding server for dev/tests on :8001).
All HTTP servers expose `/healthz` and `/readyz`.
