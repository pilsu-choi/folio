---
type: concept
title: 무상태 멀티턴 생성(/v1/chat)과 generation 설정 구현
description: PRD 범위를 검색 결과 기반 답변 생성까지 넓히고, POST /v1/chat(무상태 멀티턴, 질의 재작성, [n] 인용)과 파이프라인 YAML generation 섹션을 구현한 작업 기록
tags: [generation, llm, service, prd, multi-turn]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ../README.md
  - ../docs/INTERFACES.md
  - ../folio/generation/
  - ../service/api.py
  - ./2026-10-20-implementation-search-api.md
---

# 무상태 멀티턴 생성 구현

## 배경과 결정

- 문제: folio는 검색 결과(`hits`)까지만 돌려줬다. 고객사마다 답변 생성 서비스(프롬프트, 근거 인용, 후속 질문 처리)를 다시 만들어야 했다. 이전 범위는 [검색 API 작업 기록](2026-10-20-implementation-search-api.md)에 있다.
- 결정(사용자 승인, 2026-10-21): 생성도 지원하고 멀티턴도 포함한다.
- 결정(사용자 승인): **무상태**로 한다. App 백엔드(UI가 아니라 folio를 호출하는 고객사 서비스)가 대화 이력을 보관하고, 매 요청에 `messages` 전체를 보낸다. folio는 대화를 저장하지 않는다.
  - 근거: folio API에는 인증이 없어서 대화 소유권을 판단할 수 없다. 권한 필터를 App이 채우는 기존 구조와 같다. API 레플리카를 늘릴 때 세션 저장소가 필요 없다.
  - 비목표로 남긴 것: 대화 세션 저장, 에이전트 도구 호출 루프, 질문에서 필터 자동 추출, 스트리밍(후속 과제).
- 결정: 엔드포인트는 `POST /v1/chat` 하나다. 단일 턴은 `messages`에 user 메시지 하나만 보내면 된다.
- 결정: `system` 역할은 받지 않는다(422). 인증 없는 서비스라서, 클라이언트가 근거 지침 프롬프트를 덮어쓰지 못하게 하기 위해서다.
- 결정: `generation:`이 없으면 꺼진다. 기존 파이프라인 YAML은 바뀌지 않는다.
- 결정: 텍스트 chat 클라이언트는 `folio.generation:http` 하나로 통일했다. `folio eval testset --method llm`도 이 클라이언트를 쓴다. URL은 `--llm-url` → `FOLIO_LLM_URL` → `FOLIO_VLM_URL` 순으로 찾는다. VLM 폴백은 기존 동작과의 호환 때문에 남겼다. VLM 클라이언트(`parse/vlm.py`)는 이미지 페이로드가 달라서 그대로 뒀다.

## 동작

1. `messages`를 검증한다. 역할은 user·assistant만 허용하고, 마지막은 user여야 한다.
2. 이력을 정리한다. 이전 assistant 답변의 `[n]`은 지난 턴의 근거를 가리키므로 지운다. `max_history_tokens`(기본 2000) 안에서 오래된 턴부터 버리고, 남은 이력은 user 턴으로 시작하게 한다.
3. 이력이 있고 `rewrite: true`면 LLM을 한 번 호출해 마지막 질문을 독립 검색어(`query`)로 바꾼다. 첫 턴에서는 이 호출을 생략한다.
4. `folio.pipeline.search`로 검색한다. `/v1/search`와 같은 `retrieval:` 설정과 `required_filters`를 쓴다.
5. 근거를 `max_context_tokens`(기본 6000) 안에서 자른다. 1위 청크는 항상 넣는다. 번호를 붙인 근거를 system 메시지에 넣고 이력과 질문을 이어 답변을 받는다.
6. 답변의 `[n]` 중 `1..len(hits)` 범위인 번호만 `citations`(`{n, chunk_id, doc_id}`)로 돌려준다.

오류 코드: `generation:` 없는 파이프라인·잘못된 메시지·필수 필터 누락은 400(`ConfigError`/`ValueError`), `system` 역할·빈 목록은 422, chat 단계 실패는 502(`GenerationError`).

## 작업 내역

- `folio/generation/`
  - `http.py`: OpenAI 호환 `/chat/completions` 클라이언트. 재시도는 `folio.embed.http.post_json`을 재사용한다(기존 `_post`를 이름만 바꾸고 오류 접두어를 인자로 받게 했다).
  - `answer.py`: `answer()`, `check_messages()`, 이력·근거 토큰 예산 처리, 인용 매핑. 토큰은 `folio.chunk.tokens.count_tokens`로 센다.
- `GenerationConfig`(`folio/contracts/config.py`): `chat`(StepSpec), `system`, `rewrite`, `max_context_tokens`, `max_history_tokens`. `generation: true`는 기본값으로 켠다. `pipeline_config.schema.json`을 재생성했다.
- `GenerationError`를 `folio/errors.py`에 추가했다.
- `service/api.py`: `POST /v1/chat`을 추가했다. Search·Chat 요청의 공통 필드(`_Retrieve`), 파이프라인 로드(`_pipeline`), hit 직렬화(`_hit`)를 공유한다.
- 환경변수 `FOLIO_LLM_URL`·`FOLIO_LLM_MODEL`·`FOLIO_LLM_API_KEY`를 추가했다. 반영한 곳은 Compose `x-folio-env`·`.env.example`, Helm `external.llm.{url,model}`(API 키는 `extraEnv`), INTERFACES다.
- 문서
  - PRD: 책임 범위, 비목표, 시나리오 5, FR-31, YAML 예시, 단계 시그니처, 마일스톤, 구현 현황.
  - README: 한눈에 보기 다이어그램, 멀티턴 답변 흐름 시퀀스 다이어그램, YAML 예시, API 표, 현재 상태, 용어.
  - INTERFACES의 Generation 절, `project-template/pipelines/contracts.yaml`의 주석 처리된 `generation:` 예시.

## 검증 결과

- 단위 테스트: `pytest -q`에서 143 passed, 1 skipped(integration·slow 마커 23개 제외). 새 테스트(`tests/test_generation.py`, `tests/test_service.py::test_chat_endpoint`)는 다음을 확인한다.
  - 첫 턴은 재작성을 생략한다.
  - 후속 턴은 재작성한 `query`로 검색하고, 이력의 `[n]`을 지운다.
  - `rewrite: false`면 재작성을 하지 않는다.
  - 이력·근거 토큰 예산을 지킨다.
  - 범위를 벗어난 인용 번호를 제외한다.
  - 오류 코드는 400, 422, 502가 명세대로 나온다.
  - 필수 필터가 빠지면 거부한다.
  - HTTP 클라이언트 본문·인증 헤더와 5xx 재시도.
- `python -m folio.contracts --check`로 스키마 드리프트가 없음을 확인했다.
- `helm template`(values-dev): `external.llm`을 지정하면 `FOLIO_LLM_*` env가 렌더링되고, 지정하지 않으면 빠진다.
- 미검증: 실제 LLM 서버와의 연동, 답변 품질, Compose·kind E2E. 모두 stub chat 함수로만 확인했다. 운영 LLM은 정하지 않았다.

## 후속 과제

- 스트리밍 응답(SSE).
- 생성 품질 평가(faithfulness, 인용 정확도)를 `folio eval`에 추가.
- 개발용 결정적 LLM stub 서버(Compose 프로필).
