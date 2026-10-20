---
type: concept
title: 검색 API(/v1/search)와 retrieval 설정 구현
description: PRD 범위를 검색 API까지 넓히고, POST /v1/search, 파이프라인 YAML retrieval 섹션, 범위 필터, BM25 필드 가중치를 구현한 작업 기록
tags: [retrieval, service, prd, opensearch, milvus, pgvector]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../README.md
  - ../docs/INTERFACES.md
  - ../folio/retrieval/
  - ../service/api.py
---

# 검색 API와 retrieval 설정 구현

## 배경과 결정

- 문제: 검색은 `folio.retrieval` 라이브러리로만 제공됐다. Python이 아닌 App은 고객사마다 검색 래퍼를 새로 만들어야 했다. 검색 튜닝(모드, rerank, RRF 상수)도 코드 인자로만 바꿀 수 있었다.
- 결정(사용자 승인): PRD 범위를 "검색 인덱스까지"에서 "검색 인덱스와 검색 API까지"로 넓힌다. 답변 생성과 API 인증·인가는 계속 비목표다.
- 결정: 요청에서는 `query`, `pipeline`, `k`, `mode`, `filters`만 받는다. 필드 가중치, RRF, 후보 수, rerank, 필수 필터는 YAML 설정에만 둔다. 요청이 검색 비용이나 튜닝을 마음대로 바꾸지 못하게 하기 위해서다.
- 결정: 필드 가중치는 OpenSearch만 지원한다. Milvus는 BM25 sparse 출력 필드가 하나고, pgvector는 tsvector 컬럼이 하나라서 가중치를 줄 수 없다. 이 두 스토어에 가중치를 주면 조용히 무시하지 않고 `ConfigError`를 낸다.
- 가정: 테넌트·ACL 필터 값은 App 백엔드가 사용자 권한으로 정한다. folio API는 내부망에 두며, `required_filters`로 필터 누락만 막는다.

## 작업 내역

- `RetrievalConfig`(`folio/contracts/config.py`): `mode`, `k`, `fields`, `rrf_k`, `candidates`, `rerank`(StepSpec 재사용), `required_filters`, `fn`. `pipeline_config.schema.json`을 재생성했다.
- `folio/retrieval/base.py`
  - `check_filters`: 필드 이름이 식별자인지, 범위 키가 `gt|gte|lt|lte`인지 검사한다. 필드 이름 검사는 Milvus 표현식 주입을 막는 용도다.
  - `Retriever`가 설정값을 기본값으로 쓴다.
  - rerank는 `candidates`개를 받아 재정렬한 뒤 `k`개로 자른다.
- 범위 필터
  - OpenSearch는 `range`를 쓴다.
  - Milvus는 비교 연산자 식을 쓴다.
  - pgvector는 실제 컬럼이면 그 컬럼을 비교한다. `metadata` jsonb면 숫자 경계는 numeric으로, 문자열 경계는 text로 비교한다. 배열 컬럼에 범위를 주면 `ConfigError`다.
- OpenSearch 필드 가중치: 기본값 `{content: 1}`이면 기존 `match` 쿼리를 그대로 쓰고, 그 외에는 `multi_match`에 `field^w`를 붙인다.
- `create_retriever(..., retrieval=, sink=)`
  - 만들어 둔 sink를 재사용할 수 있다.
  - 사용자 `sink.fn`에서 sink 팩토리를 retriever로 호출하던 동작을 없앴다. 이제는 `retrieval.fn`을 요구한다.
- `folio.pipeline.retriever(cfg)`와 `search(cfg, ...)`
  - 파이프라인의 sink 캐시, 적재와 같은 embed 단계, `retrieval:` 설정으로 retriever를 만들고 캐시한다.
  - `search`는 `required_filters`를 검사한다. `None` 값을 먼저 제거하므로 `{"tenant_id": null}`로는 우회할 수 없다.
  - `folio eval run`도 같은 경로를 쓴다. `--mode`를 생략하면 YAML 설정을 따른다.
- `POST /v1/search`(`service/api.py`): `k`는 100 이하다. 알 수 없는 파이프라인, 잘못된 필터·모드, 필수 필터 누락은 400을 돌려준다.
- Compose·kind E2E 공통 시나리오(`deploy/e2e-common.sh`)에 적재 후 `/v1/search` 결과가 1건 이상인지 확인하는 단계를 추가했다.
- 문서
  - PRD: 개요·배경·목표 8·비목표·사용자·시나리오 4, FR-17 확장, FR-18 보완, FR-30 신규, YAML 계약, 권한, 마일스톤, 구현 현황.
  - README, `docs/INTERFACES.md`, `project-template/pipelines/contracts.yaml`(retrieval 예시)도 갱신했다.

## 검증 결과

- 단위 테스트: `pytest -q`에서 129 passed. 새로 추가한 테스트는 다음을 다룬다.
  - 세 스토어의 범위 필터 빌더
  - 필터 검증(주입 시도 포함)
  - OpenSearch `multi_match` 본문
  - Milvus·pgvector 가중치 거부
  - rerank 후보 → k
  - 설정 파싱과 `create_retriever`
  - `/v1/search` 200·400·422와 `required_filters`
- 통합 테스트: `pytest -m integration tests/integration/test_sinks_integration.py`에서 14 passed, 8 skipped. skip은 스토어 전용 테스트가 다른 스토어에서 건너뛴 것이다. 실제 OpenSearch·Milvus·pgvector 컨테이너에서 다음을 확인했다.
  - 정수 컬럼 범위
  - 문자열 컬럼 범위
  - metadata·dynamic 숫자 필드 범위
  - 문자열 metadata 범위
  - 위 네 가지를 hybrid·bm25·vector 세 모드에서 모두 확인
  - OpenSearch 필드 가중치로 순위가 바뀌는 것
- Compose E2E: 두 번 통과했다. 다른 세션과 겹치지 않도록 별도 프로젝트 이름(`folio-e2e-search`)과 포트(18080)를 썼고, 스택은 스크립트가 정리했다.
  - 1차: 기본 이미지 태그. 적재 `succeeded` → `POST /v1/search` 1건.
  - 2차: 커밋 `5fe6293`, 고유 태그 `FOLIO_TAG=search-e2e`. Compose는 `project-template`을 `/app/project`에 마운트하므로, 템플릿의 `retrieval:`(필드 가중치 포함) 설정으로 검색 1건을 확인했다.
  - 1차 실행이 공유 로컬 태그 `folio:0.1.0`을 이 브랜치 코드로 다시 빌드했을 수 있다. 빌드 없이(`NO_BUILD=1`) 이 태그를 쓰는 다른 worktree는 다시 빌드해야 한다.
- Helm: `helm template ... -f values-dev.yaml` 렌더링에서 api Deployment에 `FOLIO_EMBED_URL`·`OPENSEARCH_URL`·`FOLIO_CONFIG_DIR`가 들어가는 것을 확인했다. api와 worker가 같은 `folio.env`를 쓰므로, api 파드에서 하이브리드 검색에 필요한 임베딩 설정이 있다.
- kind + Helm E2E: 2026-10-21 로컬에서 통과했다(`FOLIO_TAG=search-e2e LOCAL_PORT=18081 deploy/kind/e2e.sh`).
  - 확인한 것: Helm 설치 → 적재 `succeeded` → `POST /v1/search` 1건, job 5개 처리 중 `helm upgrade`에서 유실 0, 업그레이드 후 `readyz`.
  - 안전장치: kind 노드 컨테이너에 `docker update --memory 8g --cpus 8`을 걸고, `nice -n 10`으로 돌렸다. 사용 가능한 메모리가 2GB 밑이면 클러스터를 지우는 감시를 붙였다. 노드 메모리는 최대 2.9GB였고, 끝난 뒤 클러스터를 삭제했다.
  - 이 과정에서 기존 `deploy/kind/e2e.sh` 결함 두 가지를 고쳤다.
    - `FOLIO_TAG`가 앱 이미지에만 적용돼, 구조 분석 서버·OpenSearch가 `0.1.0` 태그를 찾다가 `ImagePullBackOff`가 났다. 이제 `structure.image.tag`·`infra.opensearch.image.tag`에도 넘긴다.
    - job이 api 파드 교체보다 먼저 끝나면 마지막 `readyz` 확인이 끊긴 포트포워딩으로 접속해 실패했다. 확인 전에 포트포워딩을 다시 연결한다.
  - 앞선 시도: 1차는 클러스터 생성 직후 RBAC 경합(추정)으로 실패했고, 2차는 세션과 Docker 종료로 중단됐다.

## 미완료·후속

- API 인증은 범위 밖이다. 외부에 노출해야 하면 게이트웨이나 App 백엔드 뒤에 둔다.
- Milvus 한국어 BM25 분석기, 실제 rerank 모델 연동은 확인하지 않았다(rerank는 mock 전송으로만 테스트).

## dev 통합 (2026-10-21)

- dev가 Airflow·PP-StructureV3 GPU 작업으로 앞서 있어서, dev를 이 브랜치에 merge했다. PRD·README·`deploy/README.md`·`docs/INTERFACES.md`의 문서 충돌은 양쪽 내용을 합쳐 정리했다.
- 의미상 충돌: dev가 kind 구조 분석 이미지를 `<tag>-heuristic`으로 바꿨으므로, `deploy/kind/e2e.sh`의 `structure.image.tag`도 `$TAG-heuristic`으로 맞췄다. `helm template` 렌더링으로 확인했다.
- merge 후 확인한 것: 단위 테스트 133 passed, Sink 통합 테스트 14 passed / 8 skipped, 설정 JSON Schema 최신. kind E2E는 merge 후 다시 돌리지 않았다(이미지 태그 규칙만 바뀜).
