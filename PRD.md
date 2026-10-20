# PRD — folio: RAG Data Pipeline

Oct 20, 2026 · @pilsu

문서를 받아 검색 가능한 인덱스로 만드는 파이프라인을 knowledge-forge와 분리된 새 레포로 구축한다. 아키텍처는 Doc를 따른다.

## 배경과 문제

고객사마다 RAG 적재 파이프라인을 새로 짜고 있고, 스키마·매핑·청킹이 프로젝트 코드에 섞여 재사용이 안 된다. 새 레포는 **단계 사이의 데이터 계약만 고정한 얇은 파이프라인 라이브러리 + 최소 서비스**를 제공해, 고객사 구축은 설정 파일과 매퍼 함수 작성만으로 끝나게 한다.

- **현재 문제 1 — 재사용 불가**: 파싱·청킹·적재 코드가 고객사 요구사항과 한 덩어리라, 다음 프로젝트에서 복사 후 수정하게 된다.
- **현재 문제 2 — 느린 Parse**: VLM으로 필드를 추출하면 문서당 처리 시간이 길다. 디지털 PDF는 텍스트 레이어로 글자를 얻고, DLA로 레이아웃만 잡으면 충분하다.
- **현재 문제 3 — 재처리 비용**: 스키마나 매핑이 바뀌면 원본부터 다시 돌려 OCR까지 반복한다.
- **별도 레포인 이유**: knowledge-forge와 책임을 나눈다. 이 레포는 "문서 → 인덱스" 파이프라인과 그 계약에만 집중하고, 다른 레포는 이 레포를 라이브러리로 가져다 쓴다.

## 목표와 비목표

### 목표

1. 고객사 구축을 **스키마 파일 + 파이프라인 YAML + 매퍼 함수** 세 가지 작성만으로 끝낸다.
2. 디지털 PDF는 OCR 없이 텍스트 레이어 + DLA로 파싱하고, 스캔 페이지만 OCR로 폴백한다.
3. 같은 `run_pipeline` 함수를 API 워커, Airflow, 노트북에서 똑같이 실행한다.
4. 매핑이나 스키마가 바뀌어도 저장된 Document JSON부터 재처리해서 OCR을 다시 하지 않는다.
5. 어떤 단계든 `module:function` 하나로 교체할 수 있다. 상속·등록·플러그인 시스템은 요구하지 않는다.
6. 같은 컨테이너 이미지를 Docker(Compose) 단일 서버와 Kubernetes(Helm) 클러스터에 설정만 바꿔 배포한다. 폐쇄망에서도 같은 방식으로 설치한다.

### 비목표 (이번 범위 아님)

- Schema Registry 서비스, 스키마·매핑 편집 Admin UI
- 선언형 매핑 DSL, 플러그인 마켓·레지스트리
- 단계별 큐 분리, 오토스케일링 정책
- Kubernetes Operator·CRD, 클러스터·GPU 노드 프로비저닝 — 이미 준비된 클러스터에 Helm으로 올리는 것까지만 다룬다
- 답변 생성(LLM 응답) 서비스 — 이 레포는 검색 인덱스까지만 책임진다
- SaaS 커넥터(Slack, Notion 등) — 수집은 파일·URI 입력까지만 다룬다
- knowledge-forge 기능의 이관이나 대체

## 사용자와 핵심 시나리오

| 사용자 | 하는 일 | 이 레포에서 쓰는 것 |
| --- | --- | --- |
| 구축 개발자 | 고객사 문서를 인덱스로 적재 | `project-template`, 스키마 파일, YAML, `mappers.py`, CLI |
| 플랫폼 개발자 | 기본 parse·chunk·embed·Sink 구현과 계약 유지 | `folio/` 라이브러리, 테스트 |
| 운영 담당 | 고객사 환경(단일 서버 또는 k8s)에 설치·업그레이드 | 컨테이너 이미지, `deploy/compose`, `deploy/helm` |
| App 개발자 | 문서 업로드, 처리 상태 조회 | Ingest API |
| 평가 담당 | 청킹·매핑 변경 전후 검색 품질 비교 | Testset·Evaluate CLI |

### 시나리오 1 — 신규 고객사 구축 (하루 안에 첫 적재)

1. `project-template`을 복사한다.
2. `schemas/opensearch/contracts.json`에 도메인 필드를 추가한다.
3. `mappers.py`에 `map_contract` 함수를 작성한다.
4. `folio init` → `folio run ./samples/ --pipeline contracts`로 로컬에서 적재까지 확인한다.
5. 같은 설정으로 고객사 이미지를 빌드해 서비스를 배포한다. 단일 서버는 `docker compose up`, 클러스터는 `helm install`을 쓴다.

### 시나리오 2 — 필드 추가 후 기존 문서에 반영

1. 스키마 파일에 필드를 추가하고 `folio schema apply`를 실행한다.
2. 매퍼를 수정해 배포한다.
3. `folio reprocess --from map --index contracts`를 실행한다. 저장된 Document JSON을 쓰므로 OCR·DLA는 다시 돌지 않는다.

### 시나리오 3 — App에서 실시간 업로드

1. App이 `POST /v1/ingest`로 파일을 보내고 `job_id`를 받는다.
2. `GET /v1/jobs/{id}`로 상태를 폴링한다.
3. `succeeded`가 되면 검색에 바로 쓸 수 있다.

## 기능 요구사항

P0는 첫 릴리스에 반드시 포함, P1은 두 번째 마일스톤, P2는 요구가 확인되면 한다. 수용 기준은 레포의 샘플 문서 세트(`tests/fixtures/`)로 검증한다.

| ID | 영역 | 요구사항 | 우선순위 | 수용 기준 |
| --- | --- | --- | --- | --- |
| FR-01 | Parse | 디지털 PDF를 텍스트 레이어 + 구조 분석 백엔드 결과의 좌표 매칭으로 Document JSON으로 변환 | P0 | 디지털 PDF 샘플에서 OCR 호출 0회, 블록 텍스트가 PyMuPDF 추출 원문과 일치 |
| FR-02 | Parse | 페이지별 텍스트 레이어 품질 판정, 기준 미달 페이지만 OCR 폴백 | P0 | 스캔·깨진 폰트 페이지만 `text_source=ocr`, 나머지는 `pdf_text` |
| FR-03 | Parse | 구조 분석 백엔드의 표 구조로 셀 단위 `cells` 구성 | P0 | 샘플 표의 행·열 수와 셀 텍스트가 정답 파일과 일치 |
| FR-04 | Parse | 회전·CropBox 보정, char 단위 매칭 옵션(한글 변환 PDF) | P1 | 회전 페이지·HWP 변환 PDF 샘플에서 블록 텍스트 누락 없음 |
| FR-05 | Parse | 이미지 입력(PNG, JPG, TIFF)을 OCR 경로로 처리 | P1 | 이미지 샘플이 같은 Document JSON 형식으로 출력 |
| FR-06 | Parse | E2E VLM 백엔드 폴백(복잡 표·스캔), 그림 캡션 옵션 | P2 | 설정에서 켠 경우에만 VLM 호출, 기본값은 꺼짐 |
| FR-19 | Parse | `StructureBackend` 인터페이스 + 후보 백엔드 2종 어댑터, 벤치마크 스크립트 | P0 | 백엔드를 바꿔도 같은 샘플의 Document JSON 스키마 검증 통과, 백엔드별 정확도·속도 리포트 |
| FR-20 | Parse | DOCX·HWPX `convert` 모드(PDF 변환 후 Parse 경로), 기본값 | P1 | DOCX·HWPX 샘플이 페이지·bbox 포함 Document JSON으로 출력 |
| FR-21 | Parse | DOCX·HWPX `native` 모드(XML 직접 파싱) | P0 | 헤딩·표 구조가 원본과 일치, 모델 호출 0회 |
| FR-07 | Chunk | `auto` 전략(섹션·표·페이지·고정 길이) + 파라미터 덮어쓰기 | P0 | 문서 유형별 샘플에서 기대 전략이 선택되고, `max_tokens` 초과 청크 없음 |
| FR-08 | Map | 매퍼 함수 `(doc, chunk) -> dict` 호출, 미지정 시 공통 필드만 채움 | P0 | 매퍼가 필수 필드를 지우면 명확한 에러로 실패 |
| FR-09 | Embed | OpenAI 호환 HTTP 엔드포인트로 배치 임베딩 | P0 | 차원이 스키마와 다르면 적재 전에 실패 |
| FR-10 | Index | OpenSearch Sink `upsert`·`delete`, 같은 `doc_id` 재적재 시 이전 청크 교체 | P0 | 같은 문서를 두 번 적재해도 청크 수가 늘지 않음 |
| FR-11 | Index | pgvector Sink(App 테이블에 INSERT만, DDL 없음), Milvus Sink(collection 스키마 파일 + alias 교체) | P1 | OpenSearch와 같은 픽스처로 세 Sink 모두 적재·삭제·재적재 테스트 통과, pgvector는 App 테이블 스키마 불변 |
| FR-12 | Pipeline | `run_pipeline(uri, cfg, stop_after)` + YAML의 `module:function` 로더 | P0 | 사용자 함수로 어느 단계든 교체 가능, 문서로 예제 제공 |
| FR-13 | Pipeline | 원본·Document JSON·청크 저장, `reprocess --from parse\|chunk\|map` | P0 | `--from map` 재처리에서 구조 분석·OCR 호출 0회 |
| FR-14 | Service | Ingest API, Job 조회, Celery 워커 1종 | P0 | 업로드 → `succeeded`까지 E2E 테스트 통과, 실패 시 에러 메시지 조회 가능 |
| FR-15 | Service | Airflow 예시 DAG(PythonOperator로 `run_pipeline` 호출) | P1 | 예시 DAG가 로컬 Airflow에서 실행 |
| FR-16 | CLI | `init`, `schema apply [--reindex]`, `run`, `reprocess` | P0 | `init` 반복 실행이 안전(멱등), `--reindex`는 alias 교체로 무중단 |
| FR-17 | Retrieval | 하이브리드 검색(BM25 + 벡터) + rerank 라이브러리, OpenSearch·Milvus·pgvector 공통 인터페이스 | P1 | 필터 인자로 도메인 필드 검색 가능 |
| FR-18 | Eval | Testset 생성, Recall@k·MRR·nDCG 평가 | P2 | 설정 파일의 Git 커밋별로 결과 비교 가능 |
| FR-22 | Deploy | 컨테이너 이미지: `folio`(API·워커·CLI 공용, 실행 명령으로 역할 선택), 구조 분석 모델 서버, 고객사 이미지 빌드용 베이스 | P0 | 같은 이미지 태그가 Compose와 k8s 양쪽에서 E2E 통과, non-root 실행, 이미지 안에 비밀값 없음 |
| FR-23 | Deploy | Docker Compose 배포(앱 + PostgreSQL·RabbitMQ·OpenSearch·S3 호환 스토리지·모델 서버), GPU 서비스 선택 | P0 | 빈 서버에서 `docker compose up` 후 시나리오 3 통과 |
| FR-24 | Deploy | Helm 차트: API·워커·모델 서버 Deployment, `folio init`·`schema apply` 실행 Job(hook), ConfigMap(파이프라인 YAML)·Secret(접속 정보), GPU 리소스·nodeSelector·toleration 설정 | P0 | kind(또는 k3s) 클러스터에서 `helm install` 후 시나리오 3 통과, `helm upgrade` 중 진행 중인 job 유실 없음 |
| FR-25 | Deploy | 외부 인프라 선택: PostgreSQL·RabbitMQ·OpenSearch·Milvus·S3를 차트 내장 또는 외부 엔드포인트로 지정 | P1 | values 변경만으로 내장 ↔ 외부 전환, 앱 코드·이미지 변경 없음 |
| FR-26 | Deploy | 폐쇄망 번들: 이미지 tar(`docker save`)·Helm 차트 패키지·모델 가중치를 한 아카이브로 생성, 사설 레지스트리 지정 | P1 | 인터넷이 차단된 환경에서 번들만으로 Compose·Helm 설치 성공 |

## 계약 명세

이 레포가 하위 호환을 보장하는 것은 아래 4가지뿐이다. 변경은 SemVer 메이저 버전으로만 하고, 각 계약은 `folio/contracts/`에 타입(Pydantic 또는 TypedDict)과 JSON Schema로 둔다.

### Document JSON (parse 출력)

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `doc_id` | str | O | `source_uri + content_hash`에서 결정적으로 생성 |
| `source_uri`, `content_hash`, `mime_type` | str | O | 원본 식별 |
| `pages[].page`, `width`, `height` | int, float | O | 1부터 시작, 크기 단위 pt |
| `pages[].blocks[].type` | enum | O | `heading`, `paragraph`, `list`, `table`, `kv`, `figure`, `caption`, `header`, `footer` |
| `blocks[].text` | str | 표·그림 제외 O | 정렬·병합된 텍스트 |
| `blocks[].bbox` | \[x0, y0, x1, y1\] | O | pt, 좌상단 원점 |
| `blocks[].text_source` | enum | O | `pdf_text`, `ocr`, `native`, `vlm` |
| `blocks[].section_path` | list\[str\] | - | 상위 헤딩 경로 |
| `blocks[].cells` | list\[list\[str\]\] | table일 때 O | 병합 셀 정보는 `spans`로 별도 |
| `blocks[].key`, `value` | str | kv일 때 O | DLA가 검출한 key-value |
| `meta` | dict | - | 파서 버전, 모델 버전, 처리 시간 |

### Chunk Record (chunk·map 출력, index 입력)

| 필드 | 타입 | 필수 |
| --- | --- | --- |
| `doc_id`, `chunk_id` | str | O |
| `content` | str | O |
| `embedding` | list\[float\] | index 시점 O |
| `page`, `section_path`, `source_uri` | int, list\[str\], str | O |
| `chunk_type`, `bbox`, `contextualized_content` | - | - |
| 그 외 모든 키 | 자유 | - |

### Sink

```python
class Sink(Protocol):
    def upsert(self, records: list[dict]) -> None: ...
    def delete(self, doc_id: str) -> None: ...
```

지원 스토어는 OpenSearch(기본), Milvus, pgvector 세 가지다. 세 스토어의 차이는 Sink와 Retrieval 어댑터 안에서만 처리하고, 파이프라인 코드는 바꾸지 않는다.

| 스토어 | 스키마 정의 | 키워드 검색(BM25) | 스키마 변경 | 적합한 경우 |
| --- | --- | --- | --- | --- |
| OpenSearch | `schemas/opensearch/*.json` (index template) | 내장, 한국어는 nori 분석기 | 필드 추가 즉시, 타입 변경은 reindex + alias | 기본값, 메타데이터 필터·집계가 많을 때 |
| Milvus | `schemas/milvus/*.json` (collection 스키마) | [2.5부터 BM25 sparse 내장](https://developers.llamaindex.ai/python/framework/integrations/vector_stores/milvusfulltextsearchdemo/), 한국어 분석기 확인 필요 | 새 collection + alias 교체 | 벡터 규모가 크고 벡터 검색 성능이 우선일 때 |
| pgvector | App이 소유한 테이블 (DDL 안 함) | Postgres 전문 검색은 한국어 품질이 약함 → 벡터 위주 | App 마이그레이션에 따름 | App DB에 함께 두고 싶을 때 |

### 파이프라인 설정 (YAML)

```yaml
parse: folio.parse:default            # 또는 {fn: ..., ocr_fallback: true, dpi: 144}
chunk: {fn: folio.chunk:auto, max_tokens: 512}
map:   mappers:map_contract           # 생략 시 공통 필드만
embed: {fn: folio.embed:http, url: http://embed:8000/v1, batch_size: 64}
sink:  {type: opensearch, index: contracts}
storage: s3://folio-artifacts/       # 중간 산출물 위치
```

단계 함수 시그니처는 `parse(uri, **opts) -> DocumentJSON`, `chunk(doc, **opts) -> list[dict]`, `map(doc, chunk) -> dict`, `embed(texts, **opts) -> list[list[float]]`로 고정한다.

## 파싱 백엔드

결론: **DLA·TSR·OCR 3종 모델을 따로 띄우지 않는다. 레이아웃·표·OCR을 한 번에 처리하는 "구조 분석 백엔드" 1개를 기본으로 두고, 스캔·복잡 표용 VLM 백엔드는 선택 폴백으로 둔다.** 백엔드는 인터페이스 하나로 교체 가능하게 만들고, 기본값은 M1에서 고객사 샘플(한글 포함) 벤치마크로 확정한다.

### 후보

| 후보 | 방식 | 텍스트 레이어 활용 | 라이선스 | 이 레포에서의 용도 |
| --- | --- | --- | --- | --- |
| [Docling](https://arxiv.org/html/2408.09869v5) | 레이아웃 모델 + TableFormer, OCR은 선택 | PDF 백엔드에서 좌표 포함 텍스트 토큰을 그대로 쓴다 | MIT | 기본 백엔드 후보 — 설계 원리가 이 PRD와 같다 |
| [PaddleOCR PP-StructureV3](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html) | 레이아웃 + OCR + 표 구조·셀 검출 파이프라인, CPU/GPU | 문서상 명시 없음 → 레이아웃·표 결과만 쓰고 글자는 우리 매칭으로 | Apache-2.0 | 기본 백엔드 후보 — 한글 OCR·스캔 비중이 높으면 유리 |
| [MinerU](https://knightli.com:8443/en/2026/06/26/mineru-34-backend-modes-pipeline-hybrid-vlm/) (pipeline / hybrid) | pipeline은 전문 모델 조합, hybrid는 어려운 영역만 VLM | pipeline·hybrid 모두 네이티브 텍스트 추출이 먼저 | 확인 필요 | 대안 — hybrid 방식이 우리 폴백 구조와 같다 |
| E2E VLM 파서 (PaddleOCR-VL-1.5, GLM-OCR 등) | 페이지 이미지 → 구조화 결과 한 번에 | 사용 안 함 (페이지를 이미지로 읽음) | 모델별 확인 | 스캔·복잡 표 페이지 폴백 |

[OmniDocBench 기준(2026-05)](https://www.codesota.com/ocr/benchmark/omnidocbench) 상위권은 GLM-OCR, PaddleOCR-VL-1.5 같은 E2E VLM이다. 다만 E2E VLM은 디지털 PDF도 이미지로 다시 읽기 때문에, 텍스트 레이어로 얻는 속도·정확도 이점을 버린다. 그래서 기본 경로가 아니라 폴백으로 둔다.

### 기본 구성 (M1 벤치마크 전 가정)

1. 디지털 페이지: 텍스트 레이어 + 구조 분석 백엔드의 레이아웃·표 결과 → 좌표 매칭.
2. 스캔·품질 미달 페이지: 구조 분석 백엔드의 OCR 결과 사용.
3. 위 결과가 기준 미달인 페이지(복잡 표, 손글씨 등): 설정에서 켠 경우에만 E2E VLM 백엔드로 재처리.

모델 서빙은 많아야 2개(구조 분석 + 선택 VLM)다.

### 백엔드 인터페이스

```python
class StructureBackend(Protocol):
    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        """words가 있으면(디지털 페이지) 레이아웃·표 구조만, 없으면 OCR까지 반환"""

# PageStructure = regions[(type, bbox)] + tables[cells(bbox, row, col, span)] + ocr_words(선택)
```

좌표 매칭, Document JSON 조립, 품질 판정은 `folio.parse`가 하고, 백엔드는 "페이지에 무엇이 어디 있는가"만 답한다. 그래서 백엔드를 바꿔도 Document JSON은 변하지 않는다.

### DOCX · HWPX

두 모드를 모두 지원하고, 구조 분석을 거치는 모드를 기본값으로 한다.

| 모드 | 동작 | 장점 | 기본값 |
| --- | --- | --- | --- |
| `convert` | PDF로 변환 → 위 Parse 경로 (텍스트 레이어 + 구조 분석) | 페이지·bbox가 생겨 인용 하이라이트 가능, 모든 문서가 같은 경로 | 기본 |
| `native` | DOCX·HWPX XML을 직접 파싱 (`text_source=native`) | 빠르고 원본 구조(헤딩·표)가 정확함, 모델 호출 없음 | 옵션 |

`native` 모드는 bbox와 페이지 번호가 없을 수 있으므로 Document JSON에서 해당 필드를 선택 값으로 둔다.

HWPX → PDF 변환기는 후보를 같은 샘플로 비교해 정한다. EWL에서 쓰는 방식은 비교 후보 중 하나이고, 더 나은 방식이 있으면 그것을 쓴다. 변환기도 `convert` 옵션의 `module:function`으로 교체할 수 있게 둔다.

| 기준 | 내용 |
| --- | --- |
| 실행 환경 | Linux 컨테이너·폐쇄망에서 동작 (Windows·한글 프로그램 설치 의존이면 감점) |
| 변환 품질 | 표·쪽 나눔·글꼴이 원본과 일치, 텍스트 레이어 보존 (이미지로 래스터화하지 않음) |
| 속도·안정성 | 페이지당 변환 시간, 동시 실행 시 프로세스 충돌·멈춤 여부 |
| 라이선스 | 고객사 배포에 문제 없는지 |

후보 (2026-10 조사, 실제 샘플 검증 전):

| 후보 | 방식 | 라이선스 | 컨테이너 적합성 | 비고 |
| --- | --- | --- | --- | --- |
| [hwpforge](https://pypi.org/project/hwpforge/) | Rust 라이브러리 Python 래퍼, 한컴이 HWPX에 저장한 레이아웃을 재생해 PDF 생성 | MIT/Apache-2.0 | `pip install`만으로 동작 (Linux wheel) | 한컴에서 저장한 HWPX만 레이아웃 보유, 폰트 미설치 시 기본 실패. Beta |
| [rhwp](https://github.com/edwardkim/rhwp) | Rust 자체 조판 엔진, CLI `export-pdf`·`export-svg` | MIT | 단일 바이너리 | 활발한 개발(pre-1.0). `--text-as-paths`를 쓰면 텍스트 레이어가 사라지므로 끈다 |
| LibreOffice + [H2Orestart](https://extensions.libreoffice.org/en/extensions/show/27504) | `soffice --headless --convert-to pdf` | GPL (v0.7 이후), LibreOffice MPL | 이미지가 크고 JRE 필요, 동시 실행 시 프로세스 관리 필요 | 가장 널리 쓰이는 방식, 표 행 높이·글꼴 폭 보정이 계속 수정 중 |
| [한컴 한글 SDK](https://www.hancom.com/product/sdk/hwpSdk) | 상용 SDK의 HWP/HWPX → PDF 필터 | 상용 | Linux·컨테이너 지원 문의 필요 | 고객사 라이선스가 있을 때 선택지 |
| EWL 방식 | 확인 필요 | - | - | 위 후보와 같은 샘플로 비교 |

모든 후보에서 한글 글꼴(문서가 쓰는 글꼴 또는 대체 글꼴)을 이미지에 넣어야 하며, 비교는 변환 PDF를 같은 Parse 경로에 넣어 블록 텍스트·표 구조 정답 일치율과 페이지당 시간으로 한다.

## 배포

라이브러리·서비스 코드는 배포 방식을 모른다. Docker와 k8s의 차이는 `deploy/` 아래 설정에만 두고, 이미지와 환경변수 계약은 하나로 유지한다.

### 이미지

| 이미지 | 내용 | 실행 역할 |
| --- | --- | --- |
| `folio` | `folio/` 라이브러리 + `service/` | `api`(FastAPI), `worker`(Celery), `cli`(`folio init` 등 일회성 작업) |
| `folio-structure` | 구조 분석 백엔드 모델 서버 (CPU·GPU 태그 분리) | HTTP 추론 엔드포인트 |
| `folio-vlm` (선택) | E2E VLM 폴백 서버 | HTTP 추론 엔드포인트 |
| 고객사 이미지 | `FROM folio` + `project-template` 산출물(schemas, pipelines, `mappers.py`) | `folio`와 동일 |

- 모델 가중치는 이미지에 굽지 않고 볼륨(Compose) 또는 PVC(k8s)로 마운트한다. 폐쇄망 번들에서만 함께 묶는다.
- 설정은 파일(파이프라인 YAML·스키마)과 환경변수(접속 정보·비밀값)로만 주입한다. 같은 이미지 태그를 개발·스테이징·운영에 그대로 쓴다.
- 모든 서비스는 `/healthz`(liveness)와 `/readyz`(의존 서비스 연결 확인)를 제공하고, 워커는 SIGTERM에서 진행 중인 task를 마친 뒤 종료한다.

### Docker Compose vs Kubernetes

| 항목 | Docker Compose (`deploy/compose/`) | Kubernetes (`deploy/helm/folio/`) |
| --- | --- | --- |
| 대상 | 단일 서버, PoC, 소규모 고객사, 로컬 개발 | 고객사 k8s 클러스터, 다중 노드 |
| 설정 주입 | `.env` + 마운트한 설정 디렉터리 | ConfigMap(파이프라인 YAML·스키마), Secret(접속 정보) |
| 초기화 | `folio cli init` 일회성 서비스 | Helm `pre-install`·`pre-upgrade` hook Job |
| GPU | `deploy.resources.reservations.devices`, GPU 없는 profile 제공 | `nvidia.com/gpu` 리소스 요청, nodeSelector·toleration |
| 의존 인프라 | compose에 포함 (외부 지정 가능) | 외부 엔드포인트 기본, 개발용 내장 옵션 |
| 확장 | 워커 `--scale` 수동 | replica 수동 지정 (오토스케일링은 비목표) |
| 폐쇄망 | `docker load` + 같은 compose 파일 | 사설 레지스트리 push + `helm install` (values에 레지스트리 지정) |

## 비기능 요구사항

수치 목표는 M1에서 고객사 샘플로 기준선을 잰 뒤 확정한다. 아래는 그때까지의 원칙과 측정 방법이다.

| 영역 | 요구사항 | 측정 |
| --- | --- | --- |
| 성능 | 디지털 PDF는 OCR 경로보다 페이지당 처리 시간이 짧아야 한다. Parse 워커는 GPU 서버와 같은 네트워크에 배치한다 | 단계별 처리 시간을 `meta`와 로그에 기록, 페이지당 p50·p95 |
| 재처리 | `--from map` 재처리는 모델 호출 없이 끝난다 | 재처리 job의 DLA·OCR 호출 수 = 0 |
| 멱등성 | 같은 파일·설정의 재실행은 같은 `doc_id`·`chunk_id`를 만든다 | 재실행 후 인덱스 문서 수 불변 |
| 장애 처리 | 페이지 단위 실패가 문서 전체를 막지 않는다. 실패 페이지는 `meta.errors`에 남기고 job은 `partial` 상태 | 손상 PDF 샘플로 테스트 |
| 보안 | 폐쇄망 설치 가능(외부 API 의존 없음). 모델·DB 접속 정보는 환경변수로만 주입 | 오프라인 compose·Helm 설치로 E2E 통과 |
| 배포 | Docker Compose와 Kubernetes를 같은 이미지·같은 환경변수 계약으로 지원. 컨테이너는 non-root, 비밀값은 이미지·차트 기본값에 넣지 않음 | CI에서 Compose E2E와 kind 클러스터 Helm E2E를 모두 실행, `helm lint`·이미지 취약점 스캔 통과 |
| 권한 | `tenant_id`·`acl` 필드를 Chunk Record에 실을 수 있고, Retrieval이 필터로 적용 | 다른 테넌트 문서가 검색되지 않음 |
| 관측 | job 상태, 단계별 소요 시간, 실패 사유를 `jobs` 테이블과 구조화 로그로 남긴다 | `GET /v1/jobs/{id}`에서 단계별 상태 조회 |
| 이식성 | 라이브러리는 서비스 없이 `pip install`만으로 동작 | 노트북에서 `run_pipeline` 단독 실행 |

## 레포 구조와 기술 스택

단일 레포 안에 라이브러리, 얇은 서비스, 고객사 템플릿을 둔다. 라이브러리는 서비스에 의존하지 않는다.

```text
<repo>/
├─ folio/                    # pip 패키지 (서비스 의존성 없음)
│  ├─ contracts/             # Document JSON, Chunk Record, Sink 타입 + JSON Schema
│  ├─ parse/                 # default: 텍스트 레이어 + 구조 분석 백엔드 매칭, OCR·VLM 폴백, docx·hwpx(convert/native)
│  ├─ backends/              # StructureBackend 어댑터 (docling, ppstructure, vlm)
│  ├─ chunk/                 # auto, section, table, page, fixed
│  ├─ embed/                 # http (OpenAI 호환)
│  ├─ sinks/                 # opensearch, milvus, pgvector
│  ├─ storage.py             # 중간 산출물 저장 (S3 호환 / 로컬)
│  ├─ pipeline.py            # run_pipeline, load_config
│  ├─ retrieval/  eval/      # 독립 모듈
│  └─ cli.py                 # folio init / schema / run / reprocess
├─ service/                  # FastAPI + Celery 래퍼
├─ project-template/         # schemas/ pipelines/ mappers.py dags/ samples/
├─ deploy/
│  ├─ docker/                # Dockerfile (folio, folio-structure, 고객사 베이스)
│  ├─ compose/               # docker compose (GPU·CPU profile, 폐쇄망 override)
│  ├─ helm/folio/            # Helm 차트 (api, worker, model server, init Job)
│  └─ bundle/                # 폐쇄망 번들 생성 스크립트
└─ tests/fixtures/           # 디지털·스캔·혼합·표·회전·HWP 변환 PDF
```

| 영역 | 선택 | 비고 |
| --- | --- | --- |
| 언어 | Python 3.11+ | 타입 힌트 필수 |
| PDF | PyMuPDF | 텍스트 레이어·렌더링 |
| 구조 분석 · OCR | Docling 또는 PP-StructureV3 (M1에서 확정), E2E VLM 폴백 선택 | 모델 교체는 `parse` 옵션의 엔드포인트로 |
| API · 워커 | FastAPI, Celery, RabbitMQ | 기존 사내 스택 |
| 메타 DB | PostgreSQL | `jobs` 테이블 하나 |
| 저장소 | S3 호환 Object Storage | 로컬 파일시스템 폴백 |
| 검색 인덱스 | OpenSearch (기본), Milvus, pgvector |  |
| 배치 | Airflow (선택) | 예시 DAG만 제공 |
| 배포 | Docker 이미지, Docker Compose v2, Kubernetes + Helm 3 | 지원 k8s 최소 버전은 열린 질문 |

## 마일스톤

&#91;embedded content: 마일스톤 · 4단계, 단계별 완료 기준\]

M1에서 디지털 PDF 하나로 처음부터 끝까지 도는 것을 먼저 만들고, OCR 폴백과 서비스는 M2로 미룬다. 일정은 열린 질문에서 정한다.

## 구현 현황 (2026-10-20)

DOCX·HWPX(FR-20, FR-21, 보류)를 제외한 FR을 구현했다. 검증 상세는 [wiki/2026-10-20-implementation-core-pipeline.md](wiki/2026-10-20-implementation-core-pipeline.md)에 있다.

| 상태 | FR |
| --- | --- |
| 구현 + 수용 기준을 테스트·E2E로 확인 | FR-01~03, 05, 07~14, 16, 17, 19, 22~24, 26(Compose 오프라인 설치) |
| 구현, 부분 검증 | FR-04(합성 픽스처로 회전·CropBox·char 매칭 확인, 실제 HWP 변환 PDF 없음), FR-06(VLM은 mock), FR-18(휴리스틱 testset, LLM 생성은 mock만), FR-25(values·env 전환 렌더링만 확인) |
| 구현, 미검증 | FR-15(Airflow DAG 미실행), FR-26 Helm 사설 레지스트리 경로 |
| 보류 | FR-20, FR-21 (DOCX·HWPX) |

- 구조 분석 백엔드: 픽스처에서 heuristic·Docling·PP-StructureV3 모두 디지털 텍스트·표 결과가 같았다. OCR 유사도는 0.72 / 0.75 / 0.91, CPU 기준 초/페이지는 0.04 / 1.75 / 20.6이다. 기본 백엔드는 고객사 샘플(한글 포함)로 정한다.
- `minio/minio`가 Docker Hub에서 더 이상 배포되지 않아, 내장 S3를 digest로 고정한 `cgr.dev/chainguard/minio`로 바꿨다.

## 열린 질문

- [x] 레포·패키지 이름: `folio`로 확정 (knowledge-forge와 구분). CLI도 `folio`
- [ ] 기본 구조 분석 백엔드 확정: Docling vs PP-StructureV3를 M1에서 고객사 샘플(한글 포함)로 비교. MinerU 라이선스 확인 (어댑터·벤치마크 스크립트 구현 완료, 픽스처 결과는 위 구현 현황 참고)
- [ ] HWPX → PDF 변환기: 후보(EWL 방식 포함)를 샘플로 비교해 선택. EWL 방식을 반드시 따르지 않는다 (DOCX·HWPX는 `convert` 기본 + `native` 옵션으로 결정됨)
- [ ] 첫 적용 고객사와 샘플 문서 세트: 수용 기준과 성능 기준선의 근거가 된다
- [ ] 마일스톤별 일정과 담당자
- [ ] 라이선스·배포 형태: 사내 패키지 저장소 배포인지, 고객사에 소스로 전달하는지
- [ ] k8s 지원 범위: 최소 k8s 버전, 고객사 배포판(OpenShift 등) 호환 여부, GPU Operator 사전 설치를 전제로 할지
- [ ] 이미지 배포 경로: 사내 레지스트리 주소와 고객사 사설 레지스트리 반입 절차, 차트 저장소(OCI 레지스트리 여부)
