# folio

문서를 검색 인덱스로 만들고 검색하는 파이프라인 라이브러리와 얇은 서비스(적재·검색 API)다. 단계 사이의 데이터 계약만 고정한다. 고객사 구축은 **스키마 파일 + 파이프라인 YAML + 매퍼 함수** 세 가지로 끝낸다. 요구사항은 [PRD.md](PRD.md)에 있다.

```
parse (PDF 텍스트 레이어 + 구조 분석 백엔드, OCR·VLM 폴백) → chunk → map → embed → index (OpenSearch | Milvus | pgvector)
search (BM25 + 벡터 RRF, 필터·범위, 필드 가중치, rerank) ← POST /v1/search | folio.pipeline.search
```

## 현재 상태 (2026-10-20)

- DOCX·HWPX(FR-20, FR-21)를 제외한 PRD FR을 구현했다. DOCX·HWPX는 보류 중이며, parse에 넣으면 `NotImplementedError`가 난다.
- 입력 형식: PDF, 이미지(PNG·JPG·TIFF·BMP·WebP), PPTX, XLSX, Markdown, TXT(UTF-8·CP949 자동 판별).
- VLM: `parse.vlm: auto`가 기본이다. `FOLIO_VLM_URL`이 있으면 스캔·품질 미달 페이지, 이미지, 그림 설명, 구조가 약한 표를 VLM으로 처리하고, 없으면 OCR로 처리한다. VLM 모델은 아직 정하지 않았다(개발용 `folio vlm-stub`만 있음).
- 청킹: `adaptive`가 기본이다. `max_tokens` 안에 들어가는 가장 큰 구조 단위를 청크로 만든다. 대안은 `page`·`fixed`.
- Airflow: 예시 DAG(`project-template/dags/folio_ingest.py`)는 folio 이미지로 `folio run`을 실행한다(DockerOperator 기본, KubernetesPodOperator 선택). Airflow에는 folio 의존성이 필요 없다. Compose `airflow` 프로필로 로컬 Airflow를 띄운다. 운영 Airflow는 공식 Apache Airflow Helm 차트 기준 가이드([deploy/airflow](deploy/airflow/README.md))만 제공한다.
- 검색: `POST /v1/search`와 파이프라인 YAML `retrieval:`(모드, BM25 필드 가중치, 범위 필터, rerank, 필수 필터)을 제공한다. 상세는 [검색 API](wiki/2026-10-20-implementation-search-api.md) 작업 기록에 있다.
- 검증한 것: 단위 테스트, Sink 통합 테스트(OpenSearch·Milvus·pgvector), Compose E2E(전 형식, VLM stub 포함), Compose Airflow DAG E2E(DockerOperator), k3s 공식 Airflow 차트 설치 + KubernetesPodOperator 실행, kind + Helm E2E(업그레이드·워커 강제 종료 포함), 오프라인 번들 설치. 상세는 [코어 파이프라인](wiki/2026-10-20-implementation-core-pipeline.md), [형식·VLM·adaptive 청킹](wiki/2026-10-20-implementation-formats-vlm-adaptive.md), [Airflow 컨테이너 DAG](wiki/2026-10-20-airflow-container-dag.md) 작업 기록에 있다.
- 구조 분석: 기본은 PP-StructureV3 GPU 서버다(Compose `gpu` 프로필, Helm 기본값). GPU가 없으면 `cpu` 프로필·`values-cpu.yaml`(약 40배 느리고 메모리 약 12 GB), 개발·CI는 `dev` 프로필(heuristic)을 쓴다. 라이브러리를 단독으로 쓸 때 `FOLIO_STRUCTURE_URL`이 없으면 모델 없는 `heuristic` 백엔드를 쓴다. 상세는 [wiki/2026-10-21-ppstructure-gpu-default.md](wiki/2026-10-21-ppstructure-gpu-default.md).

## 설치

```bash
pip install -e ".[all]"          # 라이브러리 + 서비스 + 모든 Sink + S3
pip install -e ".[ocr]"          # heuristic 백엔드용 OCR(rapidocr)
pip install -e ".[docling]"      # 또는 ".[ppstructure]": 프로세스 내 구조 분석 백엔드
pip install -e ".[dev]"          # 테스트
```

라이브러리만 설치해도(`pip install -e .`) 노트북에서 `run_pipeline`을 쓸 수 있다.

## 빠른 시작 (로컬)

```bash
cp -r project-template myproject && cd myproject
export OPENSEARCH_URL=http://localhost:9200 FOLIO_EMBED_URL=http://localhost:8001/v1
folio embed-stub &                               # 개발용 임베딩 서버 (운영은 실제 엔드포인트로 교체)
folio init                                       # 저장소·jobs 테이블·인덱스 생성 (멱등)
folio run ./samples/ --pipeline contracts        # 파일당 JSON 결과 한 줄
folio schema apply [--reindex]                   # 필드 추가 → mapping 갱신, 타입 변경 → 새 인덱스 + alias 교체
folio reprocess --from map --index contracts     # 저장된 Document JSON·청크 재사용, 모델 호출 0회
folio eval testset --pipeline contracts --n 50 && folio eval run --pipeline contracts --testset testset.json
```

```python
from folio.contracts import load_config
from folio.pipeline import run_pipeline
cfg = load_config("pipelines/contracts.yaml")
result = run_pipeline("contract.pdf", cfg)

from folio.pipeline import search
hits = search(cfg, "위약금 조항", filters={"tenant_id": "acme", "article": {"gte": 3}})
```

## 파이프라인 YAML

```yaml
parse: {fn: folio.parse:default, backend: auto, vlm: auto, ocr_fallback: true, dpi: 144}
chunk: {fn: folio.chunk:adaptive, max_tokens: 1024, min_tokens: 128}   # 또는 folio.chunk:page|fixed
map:   mappers:map_contract               # 생략하면 공통 필드만 채운다
embed: {fn: folio.embed:http, batch_size: 64}
sink:  {type: opensearch, index: contracts}
retrieval:                                # 생략하면 hybrid, k=10, content만 BM25
  mode: hybrid                            # hybrid | bm25 | vector
  k: 10
  fields: {content: 1, contract_no: 3, party: 2}   # BM25 필드 가중치 (OpenSearch만)
  rrf_k: 60
  candidates: 50                          # 단계별 후보 수 (기본 max(k*3, 30)), rerank 입력
  rerank: {fn: folio.retrieval.rerank:http_rerank, top_n: 10}   # 선택, FOLIO_RERANK_URL
  required_filters: [tenant_id]           # 없으면 검색 요청 400
storage: ${FOLIO_STORAGE_URI:-.folio/artifacts}
```

필터는 `{필드: 값}`이다. 값이 스칼라면 같음, 목록이면 하나라도 일치, `{gte, gt, lte, lt}`면 범위다(숫자, ISO 날짜 문자열). 검색 로직 전체를 바꾸려면 `retrieval.fn: module:factory`를 지정한다(`factory(sink, embed, **settings)`).

어느 단계든 `module:function`으로 바꿀 수 있다. 단계 함수 시그니처와 환경변수는 [docs/INTERFACES.md](docs/INTERFACES.md)에 있다.

## 서비스

- `POST /v1/ingest` (multipart `file`, `pipeline`) → `202 {job_id}`
- `GET /v1/jobs/{id}` → `queued|running|succeeded|partial|failed`, 단계별 상태·소요 시간, 에러 메시지
- `POST /v1/search` (JSON `query`, `pipeline`, `k`≤100, `mode`, `filters`) → `{pipeline, hits: [{chunk_id, doc_id, score, content, record}]}`. 잘못된 필터·모드나 `required_filters` 누락은 400
- `DELETE /v1/documents/{doc_id}?pipeline=`
- `/healthz`, `/readyz`

API에는 인증이 없다. 내부망에 두고, App 백엔드가 사용자 권한으로 `tenant_id`·`acl` 필터를 채워 호출한다.

`folio` 이미지 실행 모드: `folio api` / `folio worker` / `folio init` / `folio structure-server` / `folio embed-stub` / `folio vlm-stub`

## 배포

- Docker Compose: `deploy/compose/` (`cp .env.example .env` → `docker compose up -d`)
- Kubernetes: `deploy/helm/folio/` (`helm install folio deploy/helm/folio -f values-dev.yaml`)
- 폐쇄망: `deploy/bundle/make_bundle.sh` → `install.sh`
- Airflow(개발용): `.env`의 `COMPOSE_PROFILES`에 `airflow` 추가, `POSTGRES_AIRFLOW_PASSWORD`·`DOCKER_GID`·`FOLIO_DOCKER_NETWORK` 설정 → UI `localhost:8081` (메타데이터는 내장 postgres의 `airflow` DB)
- Airflow(운영): 공식 Apache Airflow Helm 차트 + `deploy/airflow/values-official-chart.yaml` + `rbac-folio-namespace.yaml` ([가이드](deploy/airflow/README.md)). 외부 DB 없이 folio 내장 PostgreSQL을 Airflow 메타데이터 DB로 공유할 수 있다(`infra.postgres.extraDatabases`)

자세한 내용은 [deploy/README.md](deploy/README.md)에 있다.

## 테스트

```bash
pytest -q                                              # 단위 테스트 (모델·외부 서비스 불필요)
pytest -q -m integration tests/integration/test_sinks_integration.py   # OpenSearch·pgvector·Milvus 컨테이너 기동
deploy/compose/e2e.sh                                  # Compose E2E (적재 → 검색)
AIRFLOW=1 deploy/compose/e2e.sh                        # + Airflow DAG(DockerOperator) 실행·인덱스 적재 확인
deploy/kind/e2e.sh                                     # kind + Helm E2E (업그레이드 포함)
python scripts/bench_backends.py --backends heuristic,docling,ppstructure   # 구조 분석 백엔드 벤치마크
```

## 구조

```
folio/        contracts, parse, backends, chunk, embed, map, sinks, retrieval, eval, storage, pipeline, cli
service/      FastAPI + Celery
project-template/  schemas/ pipelines/ mappers.py dags/ samples/
deploy/       docker, compose, helm, bundle, kind
tests/        단위, 통합, fixtures (생성 스크립트: tests/fixtures/make_fixtures.py)
```
