# folio

문서를 검색 인덱스로 만드는 파이프라인 라이브러리와 얇은 서비스다. 단계 사이의 데이터 계약만 고정한다. 고객사 구축은 **스키마 파일 + 파이프라인 YAML + 매퍼 함수** 세 가지로 끝낸다. 요구사항은 [PRD.md](PRD.md)에 있다.

```
parse (PDF 텍스트 레이어 + 구조 분석 백엔드, OCR·VLM 폴백) → chunk → map → embed → index (OpenSearch | Milvus | pgvector)
```

## 현재 상태 (2026-10-20)

- DOCX·HWPX(FR-20, FR-21)를 제외한 PRD FR을 구현했다. DOCX·HWPX는 보류 중이며, parse에 넣으면 `NotImplementedError`가 난다.
- 구현은 형식·VLM·청킹 개정 이전의 PRD를 따른다. 개정 PRD의 FR-27~29(PPTX·XLSX·Markdown·TXT), VLM 기본 적용(FR-02·05·06), `adaptive` 청킹(FR-07)은 아직 반영하지 않았다. 차이는 [PRD 구현 현황](PRD.md#구현-현황-2026-10-20-featcore-pipeline)에 정리했다.
- 검증한 것: 단위 테스트, Sink 통합 테스트(OpenSearch·Milvus·pgvector), Compose E2E, kind + Helm E2E(업그레이드·워커 강제 종료 포함), 오프라인 번들 설치. 상세는 [wiki/2026-10-20-implementation-core-pipeline.md](wiki/2026-10-20-implementation-core-pipeline.md)에 있다.
- 미정: 기본 구조 분석 백엔드(Docling vs PP-StructureV3)는 고객사 샘플 벤치마크 후 정한다. `FOLIO_STRUCTURE_URL`이 없으면 모델 없는 `heuristic` 백엔드를 쓴다.

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
result = run_pipeline("contract.pdf", load_config("pipelines/contracts.yaml"))
```

## 파이프라인 YAML

```yaml
parse: {fn: folio.parse:default, backend: auto, ocr_fallback: true, dpi: 144}
chunk: {fn: folio.chunk:auto, max_tokens: 512}
map:   mappers:map_contract               # 생략하면 공통 필드만 채운다
embed: {fn: folio.embed:http, batch_size: 64}
sink:  {type: opensearch, index: contracts}
storage: ${FOLIO_STORAGE_URI:-.folio/artifacts}
```

어느 단계든 `module:function`으로 바꿀 수 있다. 단계 함수 시그니처와 환경변수는 [docs/INTERFACES.md](docs/INTERFACES.md)에 있다.

## 서비스

- `POST /v1/ingest` (multipart `file`, `pipeline`) → `202 {job_id}`
- `GET /v1/jobs/{id}` → `queued|running|succeeded|partial|failed`, 단계별 상태·소요 시간, 에러 메시지
- `DELETE /v1/documents/{doc_id}?pipeline=`
- `/healthz`, `/readyz`

`folio` 이미지 실행 모드: `folio api` / `folio worker` / `folio init` / `folio structure-server` / `folio embed-stub`

## 배포

- Docker Compose: `deploy/compose/` (`cp .env.example .env` → `docker compose up -d`)
- Kubernetes: `deploy/helm/folio/` (`helm install folio deploy/helm/folio -f values-dev.yaml`)
- 폐쇄망: `deploy/bundle/make_bundle.sh` → `install.sh`

자세한 내용은 [deploy/README.md](deploy/README.md)에 있다.

## 테스트

```bash
pytest -q                                              # 단위 테스트 (모델·외부 서비스 불필요)
pytest -q -m integration tests/integration/test_sinks_integration.py   # OpenSearch·pgvector·Milvus 컨테이너 기동
deploy/compose/e2e.sh                                  # Compose E2E
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
