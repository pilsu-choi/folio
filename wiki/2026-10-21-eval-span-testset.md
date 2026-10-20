---
type: concept
title: 구간 기반 testset과 질문 유형별 평가
description: folio eval의 정답을 chunk_id에서 원문 구간으로 바꾸고, 여러 쪽·여러 문서 질문 유형과 유형별 지표·부트스트랩 신뢰구간을 추가한 작업 기록
tags: [eval, testset, retrieval, chunking, prd]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ../README.md
  - ../folio/eval/
  - ../bench/m1/chunking/gen_testset.py
  - ../bench/m1/chunking/grid.py
  - ./2026-10-21-m1-benchmarks.md
---

# 구간 기반 testset과 질문 유형별 평가

## 배경과 결정

- 문제 1: 기존 `folio eval testset`은 청크 하나를 골라 질의를 만들고 정답을 그 `chunk_id`로 저장했다. 청킹 설정이 바뀌면 청크 번호가 달라져 testset을 쓸 수 없었다. 그래서 청킹 튜닝(예: `max_tokens` 512 vs 1024)을 이 도구로 비교할 수 없었다.
- 문제 2: `heuristic` 질의는 영어 불용어 기준이라 한국어에서 원문 문장을 거의 그대로 질의로 썼다. LLM 질의도 근거 검증이 없었다.
- 문제 3: 질문 유형이 Single-hop 하나뿐이었다. 여러 쪽·여러 문서에 근거가 흩어진 질문(비교, 시점, 다중 요구 등)을 측정할 수 없었다.
- 결정(사용자 승인, 2026-10-21): [M1 청킹 벤치마크](2026-10-21-m1-benchmarks.md)의 구간 기반 방식을 `folio eval`로 옮기고(1단계), 질문 유형과 유형별 지표를 추가한다(2단계). 기존 `chunk_id` 형식은 남기지 않고 교체했다(같은 의미의 경로를 두 개 두지 않는다).
- 원격 저장소가 없어 로컬 `dev`(`e9bea81`)에서 `feat/eval-span-testset` worktree로 작업했다.

## 구현

### testset 생성 (`folio/eval/testset.py`)

- 입력은 청크가 아니라 Document JSON(parse 출력)이다. 쪽 텍스트는 청커가 보는 leaf 텍스트(`build_leaves`)를 이어 붙인 것이다. 그래서 어떤 청킹 설정에서도 근거 구간이 어느 청크엔가 들어간다.
- 항목 형식: `{qid, type, query, answer, evidence: [{doc_id, page, text, start, end}]}`. `start`·`end`는 쪽 텍스트 기준 위치로 참고용이고, 채점은 `text`로 한다.
- 질문 유형

| 묶음 | 유형 | LLM에 주는 본문 |
| --- | --- | --- |
| 한 쪽 | `fact`, `table`, `section` | 쪽 1개 |
| 같은 문서 두 쪽 | `multi_hop`, `comparison`, `temporal`, `multi_intent` | 같은 문서에서 내용이 가장 비슷한 쪽 2개 |
| 다른 문서 두 쪽 | `multi_document`, `comparison` | 다른 문서에서 내용이 가장 비슷한 쪽 2개 |

- 비슷한 쪽은 글자 3-gram Jaccard로 고른다. 한국어 형태소 분석기 없이 동작한다. 후보는 최대 200쪽까지 무작위로 뽑아 비교한다.
- 표본 추출: 문서마다 최대 5쪽을 먼저 고르고 나머지는 무작위로 채운다. `seed`로 고정된다.
- 걸러내기(사유·유형별로 `stats`에 집계)
  - `wrong_type`: 요청하지 않은 유형
  - `no_evidence`: 질문이나 근거가 없거나 근거가 10자 미만
  - `unlocated`: 근거 인용이 원문에 그대로 없음(공백 정규화 후 비교). LLM이 지어낸 인용을 거른다
  - `one_side_only`: 두 쪽 유형인데 근거가 한 쪽에만 있음. 사실상 한 쪽 질문이라 버린다
  - `header_only`: 근거가 표 헤더뿐. 헤더는 표를 나눈 모든 청크에 반복돼서 정답 구분이 안 된다
  - `dup`: 같은 질문
- LLM 응답의 `<think>` 블록과 코드 펜스를 무시하고 JSON 배열을 찾는다. 실패하면 한 번 재시도하고, 그래도 실패한 단위는 `failed_units`로 센다. 모든 호출이 실패하면 오류를 낸다(URL·모델 설정 오류를 조용히 넘기지 않기 위해).
- `folio.generation:http`에 `extra_body`를 추가했다. vLLM Qwen의 사고 모드를 끌 때 `--llm-extra '{"chat_template_kwargs": {"enable_thinking": false}}'`로 넘긴다. OpenAI 등은 모르는 필드를 거부할 수 있어 기본값으로 넣지 않았다.
- `--method heuristic`은 LLM 없이 한 쪽 `fact`만 만든다(개발·CI용). 질의가 원문 앞부분이라 점수가 낙관적이다.

### 채점 (`folio/eval/run.py`, `metrics.py`)

- 검색된 청크가 근거 구간을 포함한다는 기준은 M1 벤치마크와 같다. 같은 문서·쪽이면서 다음 중 하나를 만족하면 된다.
  - 구간의 20자 창(10자씩 이동)이 청크 본문에 있다
  - 구간이 청크 경계에 6자 이상 걸쳐 있다
- 청크 레코드에 `pages`나 `page`가 없으면(스토어가 돌려주지 않으면) 문서만 비교한다.
- 지표: `recall@k`(찾은 근거 구간 비율), `all@k`(근거를 모두 찾은 질의 비율, 여러 근거 유형에서 중요), `mrr`, `ndcg@k`. 전체와 `by_type`, 질의별 `per_query`를 저장한다.
- `unreachable`: 인덱스의 어떤 청크도 덮지 못하는 근거가 있는 질의다. 검색이 아니라 정규화·파싱 문제로 0점이 나오는 경우를 따로 드러낸다. CLI `eval run`은 저장된 `chunks.json`으로 항상 계산한다.
- `compare`: 두 결과에서 공통 `qid`로 짝지은 부트스트랩(2000회)을 돌려 지표 차이의 95% 신뢰구간을 낸다. 전체와 유형별로 낸다.

## 검증

- 단위 테스트(`tests/test_eval.py`, 11개): 지표 계산, 가짜 LLM으로 각 걸러내기 사유, 두 쪽·다른 문서 유형, 응답 형식 오류 재시도와 전체 실패 오류, adaptive·fixed 청킹 모두 같은 testset으로 채점되고 `unreachable`이 0인지, 쪽이 다르면 0점이 되고 `unreachable`에 잡히는지, 쪽 정보가 없는 스토어, 비교 신뢰구간.
- 전체 테스트: `176 passed, 3 skipped`(통합 테스트 제외).
- 실데이터 확인(로컬, 2026-10-21): M1에서 Qwen3.8-27B로 만들고 인용 검증을 거친 한국어 질의 249개(`bench/m1/chunking/testset.jsonl`, fact 116·section 95·table 38)를 새 형식으로 바꾸고, pgx의 파싱 결과 16개 문서로 확인했다.
  - 근거 249개 모두 새 `page_texts`에서 찾아졌다. 벤치마크와 같은 쪽 텍스트를 만든다는 뜻이다.
  - 청킹 설정 4종 모두 `unreachable` 0개였다.
  - 검색기는 글자 2-gram 어휘 검색기(스크립트 안에서 만든 것)를 썼다. 아래 수치는 채점 경로 확인용이며 **청킹 설정의 품질 비교가 아니다**. 품질 비교는 [M1 벤치마크](2026-10-21-m1-benchmarks.md)(bge-m3, 토큰 예산 기준)를 따른다.

| 청킹 설정 | 청크 수 | unreachable | recall@5 | all@5 | MRR |
| --- | --- | --- | --- | --- | --- |
| adaptive 1024/128 | 538 | 0 | 0.719 | 0.715 | 0.585 |
| adaptive 512/64 | 1064 | 0 | 0.731 | 0.715 | 0.604 |
| adaptive 256/64 | 2051 | 0 | 0.723 | 0.691 | 0.614 |
| fixed 512 | 615 | 0 | 0.779 | 0.763 | 0.668 |

  - `compare`(1024/128 → 512/64): recall@5 +0.012, 95% CI [−0.019, 0.043]. 차이가 노이즈 범위라는 것을 신뢰구간이 보여 준다.
- CLI 확인: `folio eval testset --docs <dir> --method heuristic`으로 24개 생성, 연결할 수 없는 LLM URL에서는 모든 단위가 실패해 오류, 모르는 유형은 오류.

## 미완료와 다음 작업

- **미검증**: 실제 LLM으로 여러 쪽·여러 문서 유형을 생성하는 것. 작업 시점에 pgx GPU를 다른 세션(vlm-scan 측정, PP 메모리 조사)이 쓰고 있어서 27B LLM을 띄우지 않았다. 유형별 생성 수와 걸러진 비율, 질문 품질(사람이 표본 검토)을 확인해야 한다.
- 답변 평가가 필요한 유형은 다루지 않는다: Negative/Unanswerable(근거 없음 응답), Aggregation·Global/Summarization(근거가 top-k보다 많음). 운영 LLM이 정해진 뒤 `/v1/chat` 답변 평가로 추가한다.
- Conditional(질문에서 필터 추출)은 PRD 비목표라서 다루지 않는다.
- `bench/m1/chunking/`의 벤치마크 스크립트는 그대로 뒀다(토큰 예산 지표 등 벤치마크 전용 기능이 있음).
