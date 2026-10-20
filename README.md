# folio

**문서를 넣으면 검색할 수 있는 인덱스를 만들고, 그 인덱스로 검색 API와 근거를 인용하는 멀티턴 Chat API를 제공한다.**

- **고객사 구축은 파일 3개로 끝난다**: 스키마 파일, 파이프라인 YAML, 매퍼 함수
- **빠르다**: 디지털 PDF는 PDF 안의 글자를 그대로 쓰고, 스캔 페이지·그림만 VLM이나 OCR로 처리한다
- **다시 처리할 때 싸다**: 중간 결과를 저장해 두므로, 매핑이 바뀌어도 파싱·OCR을 다시 하지 않는다
- **어디서든 같은 코드로 돈다**: API 워커, Airflow, 노트북에서 같은 `run_pipeline`을 쓴다. Docker Compose와 Kubernetes에는 같은 이미지를 올린다

요구사항은 [PRD.md](PRD.md), 단계 함수 시그니처와 환경변수는 [docs/INTERFACES.md](docs/INTERFACES.md)에 있다.

## 한눈에 보기

```mermaid
flowchart LR
    F["문서<br/>PDF·이미지·DOCX·HWPX·HWP<br/>PPTX·XLSX·MD·TXT"] --> P["1 parse<br/>문서 구조 읽기"]
    P --> C["2 chunk<br/>의미 단위로 자르기"]
    C --> M["3 map<br/>도메인 필드 채우기"]
    M --> E["4 embed<br/>벡터로 바꾸기"]
    E --> I[("5 index<br/>OpenSearch·Milvus·pgvector")]
    I --> S["검색<br/>키워드 + 의미 검색"]
    App["App"] -- "POST /v1/search" --> S
    App -- "POST /v1/chat<br/>대화 이력 전체" --> G["생성 (선택)<br/>검색어 재작성 → 검색 → LLM 답변 [n]"]
    G --> S
```

| 단계 | 하는 일 | 결과물 |
| --- | --- | --- |
| parse | 페이지마다 제목·문단·표·그림을 찾아 위치와 함께 정리 | Document JSON |
| chunk | 섹션·표·목록을 깨지 않고 `max_tokens` 안에서 가장 크게 자름 | 청크 목록 |
| map | 고객사 매퍼 함수로 계약번호·당사자 같은 도메인 필드를 채움 | Chunk Record |
| embed | OpenAI 호환 임베딩 서버로 청크를 벡터로 변환 | `embedding` 필드 |
| index | 검색 저장소(Sink)에 저장. 같은 문서를 다시 넣으면 이전 청크를 교체 | 검색 인덱스 |

단계 사이에서 주고받는 데이터 형식(Document JSON, Chunk Record, Sink)만 고정돼 있다([PRD 계약 명세](PRD.md#계약-명세)). 그래서 어느 단계든 `module:function` 하나로 바꿀 수 있다. 상속이나 플러그인 등록은 필요 없다.

## 구축은 파일 3개

```mermaid
flowchart LR
    subgraph T["project-template/"]
        A["schemas/<br/>인덱스에 어떤 필드를 둘지"]
        B["pipelines/*.yaml<br/>단계별로 무엇을 쓸지"]
        D["mappers.py<br/>필드 값을 어떻게 채울지"]
    end
    T --> R["folio init → folio run"]
```

| 파일 | 정하는 것 | 예 |
| --- | --- | --- |
| `schemas/opensearch/contracts.json` | 인덱스 필드와 타입 | `contract_no: keyword` |
| `pipelines/contracts.yaml` | 단계별 함수·옵션, 저장소, 검색 튜닝 | `chunk.max_tokens: 1024` |
| `mappers.py` | 청크마다 도메인 필드 값 | `map_contract(doc, chunk) -> dict` |

## 적재 흐름 (API)

App이 파일을 올리면 API는 바로 `job_id`를 돌려주고, 실제 처리는 워커가 한다.

```mermaid
sequenceDiagram
    autonumber
    participant App
    participant API as folio API
    participant Q as RabbitMQ
    participant W as 워커
    participant DB as PostgreSQL (jobs)
    participant S3 as 파일 저장소 (S3·MinIO)
    participant Idx as 검색 저장소

    App->>API: POST /v1/ingest (파일, pipeline)
    API->>DB: job 생성 (queued)
    API->>S3: 원본 저장
    API->>Q: 작업 등록
    API-->>App: 202 {job_id}
    Q->>W: 작업 전달
    W->>W: run_pipeline (parse → chunk → map → embed)
    W->>S3: document.json · chunks.json 저장
    W->>Idx: 청크 upsert
    W->>DB: 단계마다 상태 · 소요 시간 기록
    App->>API: GET /v1/jobs/{id}
    API-->>App: succeeded | partial | failed
```

Airflow에서는 예시 DAG(`project-template/dags/folio_ingest.py`)가 folio 이미지로 `folio run`을 실행한다. Airflow 쪽에 folio를 설치할 필요가 없다.

## 파싱: 페이지마다 가장 싼 방법을 고른다

```mermaid
flowchart TD
    In["입력 파일"] --> T{"형식?"}
    T -- "PPTX · XLSX · MD · TXT" --> N["원본 구조 그대로 읽기<br/>구조 분석·OCR 없음<br/>그림만 VLM 설명"]
    T -- "DOCX · HWPX · HWP" --> X["PDF로 변환<br/>convert 서버(LibreOffice · rhwp)"]
    X --> Q
    T -- "이미지" --> IMG
    T -- "PDF" --> Q{"페이지의 글자 레이어<br/>품질이 괜찮은가?"}
    Q -- "예" --> D["PDF 글자 + 구조 분석 모델<br/>text_source = pdf_text"]
    Q -- "아니오 (스캔·깨진 폰트)" --> IMG{"VLM 서버가 있나?<br/>FOLIO_VLM_URL"}
    IMG -- "예" --> V["VLM으로 읽기<br/>text_source = vlm"]
    IMG -- "아니오" --> O["OCR로 읽기<br/>text_source = ocr"]
    D --> EN["그림·약한 표만 VLM 보강<br/>VLM 있을 때"]
```

- **구조 분석 모델**: 페이지에서 제목·문단·표 영역을 찾는 모델. 기본은 PP-StructureV3 GPU 서버다. GPU가 없으면 CPU 버전(약 40배 느림, 메모리 약 12 GB), 개발·CI는 모델 없는 `heuristic`을 쓴다. 라이브러리 단독 사용 시 `FOLIO_STRUCTURE_URL`이 없으면 `heuristic`이다.
- **DOCX·HWPX·HWP**: 기본은 PDF로 변환한 뒤 PDF 경로를 탄다(`docx_mode`·`hwpx_mode: convert`). 페이지 번호와 bbox가 생기지만, 그 좌표는 변환된 PDF 기준이며, 인용 하이라이트용으로 그 PDF를 원본 옆에 `converted.pdf`로 저장한다(`meta.convert.pdf_key`·`pdf_hash`). 변환은 `folio-convert` 서버(`FOLIO_CONVERT_URL`)가 맡고, URL이 없으면 로컬 LibreOffice·rhwp를 쓴다. DOCX는 LibreOffice, HWPX·HWP는 rhwp(실패 시 LibreOffice + H2Orestart)다. 선택 근거는 [DOCX](wiki/2026-10-21-docx-converter-benchmark.md)·[HWPX](wiki/2026-10-20-hwpx-converter-benchmark.md) 벤치마크에 있다. XML을 직접 읽는 `native` 모드(FR-21)는 아직 없다.
- **VLM**: `parse.vlm: auto`가 기본이다. `FOLIO_VLM_URL`이 없으면 VLM 대신 OCR을 쓴다. 운영용 VLM 모델은 아직 정하지 않았다(개발용 `folio vlm-stub`만 있음).
- **청킹**: 기본 `adaptive`. 대안은 `page`, `fixed`.

## 검색 흐름

```mermaid
flowchart LR
    Q["질의 + 필터"] --> F{"required_filters<br/>충족?"}
    F -- "아니오" --> E400["400 에러"]
    F -- "예" --> B["키워드 검색<br/>BM25"]
    F -- "예" --> V["의미 검색<br/>벡터"]
    B --> R["순위 합치기<br/>RRF"]
    V --> R
    R --> RR["재정렬 (선택)<br/>rerank"]
    RR --> H["상위 k개<br/>hits"]
```

- **모드**: `hybrid`(기본, 둘 다), `bm25`(키워드만), `vector`(의미만)
- **필터**: 값이 하나면 같음, 목록이면 하나라도 일치, `{gte, gt, lte, lt}`면 범위(숫자, ISO 날짜)
- **필수 필터**: YAML `required_filters: [tenant_id]`를 두면 빠진 요청을 막는다. API에는 인증이 없으므로 내부망에 두고, App 백엔드가 사용자 권한으로 `tenant_id`·`acl`을 채워 호출한다
- 검색 튜닝을 바꿔도 App 코드는 그대로다. YAML `retrieval:`만 고친다

## 멀티턴 답변 흐름 (선택)

파이프라인 YAML에 `generation:`이 있으면 `POST /v1/chat`을 쓸 수 있다. folio는 대화를 저장하지 않는다. App이 매 요청에 지금까지의 대화 전체를 보낸다.

```mermaid
sequenceDiagram
    autonumber
    participant App
    participant API as folio API
    participant LLM as LLM (OpenAI 호환)
    participant Idx as 검색 저장소

    App->>API: POST /v1/chat {messages: [user, assistant, …, user], filters}
    opt 이전 대화가 있을 때 (첫 턴은 생략)
        API->>LLM: 마지막 질문을 독립 검색어로 재작성
        LLM-->>API: query
    end
    API->>Idx: search(query, filters) — /v1/search와 같은 경로·필수 필터
    Idx-->>API: hits
    API->>LLM: system(지침 + 번호 붙인 근거 [1]…[n]) + 이력 + 질문
    LLM-->>API: 답변 "… [1]"
    API-->>App: {answer, query, citations: [{n, chunk_id, doc_id}], hits}
```

- **이력 정리**: 이전 답변의 `[n]` 표시는 지난 턴의 근거를 가리키므로 지운다. 이력과 근거는 `max_history_tokens`·`max_context_tokens` 안에서 오래된 턴, 낮은 순위 청크부터 버린다
- **인용**: 답변의 `[n]`은 `hits[n-1]`이다. 범위를 벗어난 번호는 `citations`에서 뺀다
- **보안**: `system` 역할은 받지 않는다(422). 권한 필터는 검색과 같이 App이 채운다
- LLM 단계는 `generation.chat: module:function`(`fn(messages, **opts) -> str`)으로 바꿀 수 있다. 기본은 `FOLIO_LLM_URL`의 OpenAI 호환 서버(vLLM 등)이고, Claude API는 `folio.generation.anthropic:chat`(`pip install -e ".[anthropic]"`, `ANTHROPIC_API_KEY`)

## 채팅 App (선택)

`apps/chat`은 `/v1/chat` 위에 얹은 참조 App이다. 사용자는 SSO로 로그인하고, 대화는 App의 DB에 저장된다. folio API는 인증이 없으므로 내부망에 두고, 사용자는 App만 접속한다.

```mermaid
flowchart LR
    U["브라우저<br/>단일 HTML UI"] -- "세션 쿠키<br/>/api/*" --> A["채팅 App<br/>apps/chat"]
    U -. "로그인 리다이렉트" .-> IdP["OIDC IdP<br/>Keycloak·Entra ID 등"]
    A -- "코드 교환·ID 토큰 검증" --> IdP
    A <--> DB[("App DB<br/>users·conversations·messages")]
    A -- "POST /v1/chat<br/>이력 + 권한 필터(클레임)" --> F["folio API<br/>(내부망)"]
    A -- "GET /v1/documents/{id}/pdf<br/>근거 원문" --> F
    F --> S[("검색 저장소")]
    F --> L["LLM<br/>vLLM(OpenAI 호환)·Claude"]
```

```mermaid
sequenceDiagram
    autonumber
    participant U as 브라우저
    participant A as 채팅 App
    participant F as folio API
    U->>A: POST /api/conversations/{id}/messages {content}
    A->>A: DB에서 이력 복원(최근 50개) · filters = 클라이언트 값 + 로그인 ACL(우선)
    A->>F: POST /v1/chat {messages, pipeline, filters}
    F-->>A: {answer, query, citations, hits}
    A->>A: 질문·답변을 함께 저장 (실패하면 저장하지 않음)
    A-->>U: 201 {messages, conversation}
```

- **로그인**: `FOLIO_APP_AUTH=oidc`면 OIDC 코드 플로(PKCE, ID 토큰 서명·nonce 검증)로 로그인한다. 세션은 서명된 쿠키(HttpOnly, SameSite=Lax)다. 기본값은 `oidc`이고, 로그인 없는 개발용 `dev` 모드(고정 사용자)는 명시해야 켜진다
- **권한**: `FOLIO_APP_ACL_CLAIMS='{"tenant_id": "tenant"}'`이면 토큰의 `tenant` 클레임이 검색 필터 `tenant_id`가 된다. 클라이언트가 같은 키를 보내도 덮어쓰고, 클레임이 없는 사용자는 검색할 수 없다(403)
- **대화 API**: 대화 목록·생성·조회·이름 변경·삭제, 메시지 전송, 답변 피드백(`up`·`down`), "이 질문부터 삭제". 다른 사용자의 대화는 404
- **원문 하이라이트**: 근거 카드의 "원문 보기"를 누르면 App이 folio에서 그 문서의 PDF를 받아, 인용된 쪽의 근거 영역(`bbox`)을 칠하고 주변까지 잘라 이미지로 보여 준다. 이미지를 누르면 PDF가 그 쪽에서 열린다. 사용자는 자기 답변의 근거로 나온 문서만 열 수 있다. 변환한 DOCX·HWPX·HWP도 저장된 변환 PDF로 표시된다
- **UI**: 대화·근거 패널을 데스크톱에서 각각 접고 상태를 저장하며, 1100px 이하에서는 배경 클릭·Escape 닫기와 포커스 관리를 지원하는 서랍으로 연다. 검색 대상과 수정 후 전송할 질문 예시를 제공하고, 근거 점수·메타데이터는 상세 정보에 모았다. 쉬운 설정 용어를 사용하며 검색 대상은 새 대화에서 바꾼다. `[n]` 인용·원문 쪽 하이라이트, 재작성된 검색어, 라이트·다크를 지원한다. 외부 리소스 없이 동작한다 ([UI 작업 기록](wiki/2026-10-21-chat-ui-panels.md))

```bash
pip install -e ".[app]"
folio api &                                                  # :8080, generation: 이 켜진 파이프라인
FOLIO_APP_AUTH=dev FOLIO_API_URL=http://localhost:8080 uvicorn --factory apps.chat.main:create_app --port 8000
# SSO: FOLIO_APP_AUTH=oidc OIDC_ISSUER=https://idp/realms/x OIDC_CLIENT_ID=folio-chat OIDC_CLIENT_SECRET=...
#      FOLIO_APP_SECRET_KEY=... (redirect URI: <app>/auth/callback)
```

환경변수와 API 전체는 [docs/INTERFACES.md](docs/INTERFACES.md#chat-app-appschat-extra-folioapp)에 있다. 이미지·Compose·Helm에는 아직 넣지 않았다.

## 재처리: 바뀐 단계부터만 다시

각 단계 결과를 저장해 두므로, 바뀐 단계부터 다시 돌린다.

```mermaid
flowchart LR
    src[("저장된 원본")] --> P["parse"] --> dj[("document.json")] --> C["chunk"] --> cj[("chunks.json")] --> M["map"] --> E["embed"] --> I["index"]
    R0(["--from parse<br/>파서 변경"]) -.-> src
    R1(["--from chunk<br/>청킹 설정 변경"]) -.-> dj
    R2(["--from map<br/>매퍼·스키마 변경"]) -.-> cj
```

```bash
folio schema apply [--reindex]                   # 필드 추가 → mapping 갱신, 타입 변경 → 새 인덱스 + alias 교체(무중단)
folio reprocess --from map --index contracts     # 저장된 청크 재사용, 파싱·OCR 호출 0회
```

## 배포: 같은 이미지, 두 가지 방식

```mermaid
flowchart TB
    img["folio 이미지<br/>api · worker · CLI 공용"]
    st["구조 분석 서버 이미지<br/>gpu · cpu · heuristic"]
    cv["변환 서버 이미지 folio-convert<br/>DOCX·HWPX·HWP → PDF"]
    img --> CP["Docker Compose<br/>단일 서버"]
    img --> HM["Helm<br/>Kubernetes"]
    st --> CP
    st --> HM
    cv --> CP
    cv --> HM
    CP --> INF[("PostgreSQL · RabbitMQ · OpenSearch · MinIO<br/>내장 또는 외부")]
    HM --> INF
    BD["폐쇄망 번들<br/>이미지 tar + 차트 + 모델"] -.-> CP
    BD -.-> HM
```

| 방식 | 명령 | 비고 |
| --- | --- | --- |
| Docker Compose | `cd deploy/compose && cp .env.example .env && docker compose up -d` | 프로필: `infra` + `gpu`(기본) \| `cpu` \| `dev` |
| Kubernetes | `helm install folio deploy/helm/folio -f values-dev.yaml` | GPU 없으면 `values-cpu.yaml` |
| 폐쇄망 | `deploy/bundle/make_bundle.sh` → `install.sh` | 인터넷 없이 Compose·Helm 설치 |
| Airflow(개발) | `.env`의 `COMPOSE_PROFILES`에 `airflow` 추가 | `POSTGRES_AIRFLOW_PASSWORD`·`DOCKER_GID`·`FOLIO_DOCKER_NETWORK` 설정, UI `localhost:8081`, 메타데이터는 내장 postgres의 `airflow` DB |
| Airflow(운영) | 공식 Apache Airflow Helm 차트 | `deploy/airflow/values-official-chart.yaml` + `rbac-folio-namespace.yaml`, folio 내장 PostgreSQL 공유 가능(`infra.postgres.extraDatabases`). [가이드](deploy/airflow/README.md) |

자세한 내용은 [deploy/README.md](deploy/README.md)에 있다.

## 빠른 시작 (로컬)

```bash
pip install -e ".[all]"                          # 라이브러리 + 서비스 + 모든 Sink + S3

cp -r project-template myproject && cd myproject
export OPENSEARCH_URL=http://localhost:9200 FOLIO_EMBED_URL=http://localhost:8001/v1
folio embed-stub &                               # 개발용 임베딩 서버 (운영은 실제 엔드포인트)
folio init                                       # 저장소·jobs 테이블·인덱스 생성 (여러 번 실행해도 안전)
folio run ./samples/ --pipeline contracts        # 파일당 JSON 결과 한 줄
```

노트북에서는 라이브러리만(`pip install -e .`) 설치해도 된다.

```python
from folio.contracts import load_config
from folio.pipeline import run_pipeline, search

cfg = load_config("pipelines/contracts.yaml")
result = run_pipeline("contract.pdf", cfg)
hits = search(cfg, "위약금 조항", filters={"tenant_id": "acme", "article": {"gte": 3}})
```

<details>
<summary><b>설치 옵션 전체</b></summary>

```bash
pip install -e ".[all]"          # 라이브러리 + 서비스 + 모든 Sink + S3
pip install -e ".[ocr]"          # heuristic 백엔드용 OCR(rapidocr)
pip install -e ".[docling]"      # 또는 ".[ppstructure]": 프로세스 내 구조 분석 백엔드
pip install -e ".[app]"          # 채팅 App(apps/chat): Authlib, itsdangerous
pip install -e ".[anthropic]"    # Claude API chat 단계
pip install -e ".[dev]"          # 테스트
```

</details>

<details>
<summary><b>파이프라인 YAML 전체 예시</b></summary>

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
generation:                               # 선택, 있으면 POST /v1/chat 사용 (true = 기본값)
  chat: {fn: folio.generation:http, max_tokens: 1024}   # FOLIO_LLM_URL / FOLIO_LLM_MODEL
  # chat: {fn: "folio.generation.anthropic:chat", effort: low}   # Claude API (ANTHROPIC_API_KEY)
  rewrite: true                           # 후속 질문을 독립 검색어로 재작성
  max_context_tokens: 6000                # 근거 청크 예산
  max_history_tokens: 2000                # 대화 이력 예산 (오래된 턴부터 버림)
storage: ${FOLIO_STORAGE_URI:-.folio/artifacts}
```

검색 로직 전체를 바꾸려면 `retrieval.fn: module:factory`를 지정한다(`factory(sink, embed, **settings)`).

</details>

<details>
<summary><b>API · 실행 모드 · 평가</b></summary>

| 엔드포인트 | 설명 |
| --- | --- |
| `POST /v1/ingest` | multipart `file`, `pipeline` → `202 {job_id}` |
| `GET /v1/jobs/{id}` | `queued` \| `running` \| `succeeded` \| `partial` \| `failed`, 단계별 상태·소요 시간, 에러 메시지 |
| `POST /v1/search` | JSON `query`, `pipeline`, `k`(≤100), `mode`, `filters` → `{pipeline, hits: [{chunk_id, doc_id, score, content, record}]}` (`record`에서 `embedding`은 뺀다). 잘못된 필터·모드, 필수 필터 누락은 400 |
| `POST /v1/chat` | JSON `messages`(`user`·`assistant` 대화 전체, 마지막은 `user`), `pipeline`, `k`, `mode`, `filters` → `{pipeline, answer, query, citations: [{n, chunk_id, doc_id}], hits}`. 무상태라 이력은 App이 보관한다. `generation:` 없는 파이프라인·필수 필터 누락 400, `system` 역할 422, LLM 실패 502 |
| `GET /v1/documents/{doc_id}/pdf?pipeline=` | 인용 하이라이트용 PDF. hit의 `page`·`bbox`(pt, 왼쪽 위 원점)가 가리키는 PDF다. 변환 문서는 `converted.pdf`, PDF 입력은 원본을 준다. 쪽이 없는 형식은 404. 접근 권한은 App이 판단한다 |
| `DELETE /v1/documents/{doc_id}?pipeline=` | 인덱스의 청크와 저장된 중간 결과 삭제 |
| `/healthz`, `/readyz` | 상태 확인 |

`folio` 이미지 실행 모드: `folio api` / `folio worker` / `folio init` / `folio structure-server` / `folio embed-stub` / `folio vlm-stub`. 변환 서버는 별도 이미지 `folio-convert`(`folio convert-server`, :9100)다.

평가(구간 기반 testset, 유형별 Recall@k·All@k·MRR·nDCG). 정답을 청크 번호가 아니라 **원문 구간**(문서·쪽·문장)으로 저장해서, 청킹 설정을 바꿔도 같은 testset으로 비교한다.

```mermaid
flowchart LR
  D[document.json<br/>parse 출력] -->|쪽 또는 비슷한 쪽 2개| L[LLM: 유형별 질문<br/>+ 근거 원문 인용]
  L -->|인용이 원문에 그대로 있는지 확인<br/>표 헤더만·한쪽만 인용 제거| T[testset.json<br/>query · type · evidence]
  T --> R[folio eval run<br/>같은 retrieval: 설정으로 검색]
  R -->|검색된 청크가 근거 구간을 포함하나| M[지표: 전체 · 유형별<br/>unreachable 진단]
  M --> C[folio eval compare<br/>질의 단위 부트스트랩 95% CI]
```

```bash
folio eval testset --pipeline contracts --pages 50 --pairs 20 \
  --llm-extra '{"chat_template_kwargs": {"enable_thinking": false}}'   # FOLIO_LLM_URL/MODEL, vLLM이면 사고 모드 끔
folio eval run --pipeline contracts --testset testset.json            # 결과: eval-out/<설정 커밋>.json
folio eval compare eval-out/<a>.json eval-out/<b>.json
```

질문 유형: 한 쪽 `fact`·`table`·`section`, 같은 문서의 두 쪽 `multi_hop`·`comparison`·`temporal`·`multi_intent`, 서로 다른 문서 `multi_document`·`comparison`. `--types`로 고른다. `--method heuristic`은 LLM 없이 `fact`만 만든다(개발·CI용). 답변 품질(근거 없음 응답, 집계·요약형 질문)은 아직 평가하지 않는다.

</details>

## 현재 상태 (2026-10-21)

| 영역 | 상태 |
| --- | --- |
| 입력 형식 | ✅ PDF, 이미지(PNG·JPG·TIFF·BMP·WebP), PPTX, XLSX, Markdown, TXT(UTF-8·CP949 자동 판별) |
| DOCX·HWPX·HWP | ✅ `convert` 모드(PDF 변환 후 파싱, FR-20) ([작업 기록](wiki/2026-10-21-docx-hwpx-convert-mode.md)). ⏸️ `native` 모드(FR-21) 보류. PPTX도 `pptx_mode="convert"`로 같은 변환 서버를 쓸 수 있다(픽스처로 확인, 기본은 `native`). 한국어 어절은 공백 글리프가 없어도 글자 간격으로 나눈다 |
| VLM | 🟡 연동 완료, 운영 모델 미정(개발용 stub만 있음) |
| 검색 API | ✅ `POST /v1/search` + YAML `retrieval:` ([작업 기록](wiki/2026-10-20-implementation-search-api.md)) |
| 평가 | 🟡 구간 기반 testset·유형별 지표·부트스트랩 CI. M1 한국어 질의 249개로 채점 경로 확인, 실제 LLM 생성(다중 쪽 유형 포함) 미확인 ([작업 기록](wiki/2026-10-21-eval-span-testset.md)) |
| 생성(Chat API) | 🟡 `POST /v1/chat` 무상태 멀티턴 + YAML `generation:`. stub LLM으로만 검증, 운영 LLM 미정, 스트리밍 미지원 ([작업 기록](wiki/2026-10-21-generation-multiturn.md)) |
| 채팅 App | 🟡 `apps/chat`: SSO(OIDC)·대화 저장·UI. 실제 `/v1/chat`(stub LLM)·로컬 테스트용 OIDC 서버로 검증, 실제 IdP·LLM·배포 미확인 ([작업 기록](wiki/2026-10-21-chat-app-sso.md)) |
| 구조 분석 | ✅ 기본 PP-StructureV3 GPU ([작업 기록](wiki/2026-10-21-ppstructure-gpu-default.md)) |
| Airflow | ✅ 예시 DAG(DockerOperator 기본, KubernetesPodOperator 선택) ([작업 기록](wiki/2026-10-20-airflow-container-dag.md)) |

검증한 것: 단위 테스트, Sink 통합 테스트(OpenSearch·Milvus·pgvector), Compose E2E(전 형식, VLM stub 포함, DOCX·HWPX 변환 서버 경로), Compose Airflow DAG E2E, k3s 공식 Airflow 차트 + KubernetesPodOperator, kind + Helm E2E(업그레이드·워커 강제 종료 포함), 오프라인 번들 설치. 상세는 [코어 파이프라인](wiki/2026-10-20-implementation-core-pipeline.md), [형식·VLM·adaptive 청킹](wiki/2026-10-20-implementation-formats-vlm-adaptive.md) 작업 기록에 있다.

## 테스트

```bash
pytest -q                                              # 단위 테스트 (모델·외부 서비스 불필요)
pytest -q -m integration tests/integration/test_sinks_integration.py   # OpenSearch·pgvector·Milvus 컨테이너 기동
deploy/compose/e2e.sh                                  # Compose E2E (적재 → 검색)
AIRFLOW=1 deploy/compose/e2e.sh                        # + Airflow DAG(DockerOperator) 실행·인덱스 적재 확인
deploy/kind/e2e.sh                                     # kind + Helm E2E (업그레이드 포함)
python scripts/bench_backends.py --backends heuristic,docling,ppstructure   # 구조 분석 백엔드 벤치마크
```

## 폴더 구조

```
folio/             라이브러리: contracts, parse, backends, chunk, embed, map, sinks, retrieval, generation, eval, storage, pipeline, cli
service/           API(FastAPI) + 워커(Celery)
apps/chat/         참조 채팅 App: SSO, 대화 저장 API, 단일 HTML UI
project-template/  고객사 구축 템플릿: schemas/ pipelines/ mappers.py dags/ samples/
deploy/            docker, compose, helm, bundle, kind, airflow
tests/             단위, 통합, fixtures (생성 스크립트: tests/fixtures/make_fixtures.py)
```

## 용어

| 용어 | 뜻 |
| --- | --- |
| Document JSON | parse 결과. 페이지별 블록(제목·문단·표·그림)과 위치, 글자 출처(`pdf_text`·`ocr`·`native`·`vlm`) |
| Chunk Record | 검색 저장소에 들어가는 한 행. `content`, `embedding`, 페이지, 섹션 경로 + 도메인 필드 |
| Sink | 검색 저장소 어댑터. `upsert`·`delete` 두 함수만 있다 |
| 구조 분석 백엔드 | 페이지 이미지에서 제목·문단·표 영역을 찾는 모델(PP-StructureV3, Docling, heuristic) |
| VLM | 이미지를 보고 글로 설명하는 모델. 스캔 페이지 읽기, 그림 설명, 복잡한 표에 쓴다 |
| BM25 | 단어가 얼마나 겹치는지로 매기는 키워드 검색 점수 |
| RRF | 여러 검색 결과의 순위를 하나로 합치는 방법 |
| rerank | 후보를 별도 모델로 다시 정렬해 정확도를 높이는 단계 |
| 질의 재작성 | "그 사유는?" 같은 후속 질문을 대화 맥락을 넣어 혼자서도 검색되는 질의로 바꾸는 단계 |
