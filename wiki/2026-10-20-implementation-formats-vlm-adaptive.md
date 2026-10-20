---
type: concept
title: 새 입력 형식, VLM 기본 적용, adaptive 청킹 구현
description: 개정 PRD의 FR-27~29, FR-02·05·06(VLM 기본), FR-07(adaptive)을 구현하고 검증한 작업 기록
tags: [implementation, parse, pptx, xlsx, markdown, vlm, chunking, adaptive]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../docs/INTERFACES.md
  - ./2026-10-20-implementation-core-pipeline.md
  - ./2026-10-20-prd-formats-vlm-adaptive-chunking.md
---

# 새 입력 형식, VLM 기본 적용, adaptive 청킹 구현

[코어 파이프라인 구현](./2026-10-20-implementation-core-pipeline.md) 이후, 개정 PRD와의 차이 세 가지를 반영했다. 작업은 최신 `dev`에서 만든 `feat/formats-adaptive-vlm` 브랜치(worktree `../folio-wt/formats-adaptive-vlm`)에서 했다.

## 사용자 결정

- FR-27~29 구현, `adaptive` 청킹 기본값: 사용자 지시.
- VLM 기본 적용 방식: `vlm: auto`를 제안했고 그대로 진행했다. VLM 엔드포인트(`FOLIO_VLM_URL`)가 있으면 PRD대로 VLM이 기본이고, 없으면 OCR 경로로 동작한다.

## 구현 내역

| 영역 | 내용 | 위치 |
| --- | --- | --- |
| 계약 | `code` 블록 타입. Chunk Record `pages`. `chunk_id` = `doc_id` + 블록 범위 + 전략·파라미터 해시(`settings_hash`). 기본 chunk 함수를 `folio.chunk:adaptive`로 변경 | `folio/contracts/` |
| PPTX (FR-27) | 슬라이드 = 페이지. 제목은 heading, 텍스트는 paragraph·list, 표는 cells·spans, 그림은 figure(VLM 설명). 발표자 노트는 옵션. bbox는 도형 위치(EMU → pt) | `folio/parse/pptx.py` |
| XLSX (FR-28) | 시트 = 페이지. 시트 이름이 `section_path`. 비어 있지 않은 연속 영역이 table(병합은 spans, 수식은 저장된 값). 숨김 시트 제외가 기본 | `folio/parse/xlsx.py` |
| Markdown·TXT (FR-29) | 헤딩·목록·GFM 표·코드·이미지 대체 텍스트. TXT는 빈 줄 단위. 인코딩은 BOM → UTF-8 → CP949 → charset-normalizer 순으로 판별 | `folio/parse/text.py` |
| 이미지 (FR-05) | BMP·WebP 추가 | `folio/parse/core.py` |
| VLM (FR-02·05·06) | 아래 별도 정리 | `folio/parse/core.py`, `folio/parse/vlm.py`, `folio/backends/vlm_stub.py` |
| 청킹 (FR-07) | `adaptive`(기본), `page`, `fixed`. 이전 `auto`·`section`·`table`은 제거 | `folio/chunk/` |
| CLI·배포 | 지원 확장자 추가, `folio vlm-stub`, Compose `vlm-stub` 프로필, Helm `vlmStub`, 템플릿 YAML을 `adaptive`(1024/128)·`vlm: auto`로 변경 | `folio/cli.py`, `deploy/`, `project-template/` |

`vlm: auto`의 동작:

- `FOLIO_VLM_URL`이 있을 때만 켜진다. 끄면 `meta.vlm.reason`에 사유를 남긴다.
- VLM이 처리하는 대상:
  - 이미지 파일과 품질 미달 PDF 페이지: 페이지 단위로 처리하고 `text_source=vlm`.
  - 디지털 페이지 안의 figure 영역: 잘라서 설명을 받는다.
  - 구조가 약한 표: 잘라서 GFM 표로 다시 읽는다.
- 디지털 텍스트만 있는 문서는 VLM 호출이 0회다.
- 호출이 실패하면 해당 페이지만 OCR로 폴백하고 `meta.vlm_fallbacks`에 기록한다. `meta.errors`에는 OCR 폴백까지 실패한 경우만 남긴다.

`adaptive` 청킹의 동작:

- 섹션 트리를 위에서부터 내려가며 `max_tokens` 안에 들어가는 가장 큰 단위를 청크로 만든다.
- 블록 안에서 나눌 때는 블록 종류별 경계를 따른다.

  | 블록 | 분할 경계 | 보존 규칙 |
  | --- | --- | --- |
  | paragraph | 문장 | |
  | list | 항목 | 하위 항목은 상위 항목과 함께 |
  | table | 행 묶음 | 헤더 반복, rowspan 행은 함께 |
  | code | 빈 줄 | |
  | figure | 나누지 않음 | 캡션과 함께 |

- `min_tokens`보다 작은 형제는 합치되, 부모 섹션 경계는 넘지 않는다.
- 헤딩만 있는 청크는 만들지 않는다.
- `max_tokens`는 임베딩 입력인 `contextualized_content`(섹션 경로 + 본문) 기준으로 지킨다.
- 알 수 없는 옵션은 `ConfigError`로 거부한다.

## 검증 결과 (실제 실행)

- 단위 테스트: `pytest -q` → 123개 통과. 그중 새로 추가한 테스트:
  - 형식: 7개
  - VLM: 10개
  - 청킹: 34개로 다시 작성
- 로컬 실행(OpenSearch + 임베딩 stub + VLM stub): PDF, 스캔 혼합 PDF, 그림 PDF, PPTX, XLSX, Markdown, CP949 TXT, PNG, WebP 10개 파일이 모두 `succeeded`. 모델 호출 수는 다음과 같다.

  | 파일 | 모델 호출 |
  | --- | --- |
  | 디지털 PDF | VLM 0회 |
  | 스캔 페이지 2개 | VLM 2회 |
  | 그림 PDF | VLM 1회 |
  | 이미지 | VLM 1회씩 |
  | PPTX 그림 | VLM 1회 |
  | XLSX·Markdown·TXT | 모델 호출 0회 |

- Compose E2E(`COMPOSE_PROFILES=infra,cpu,vlm-stub`, `FOLIO_VLM_URL=http://vlm:8002/v1`):
  - 기본 시나리오가 통과했다.
  - API로 형식별 8개 파일을 올렸고 모두 `succeeded`. VLM stub은 6회 호출됐다.
- `helm lint`·`helm template`(values-dev, vlmStub 포함) 통과. `docker compose config` 통과.

## 미완료·미검증

- 실제 VLM 모델로는 검증하지 않았다. 모델 선정은 M1 열린 질문이고, 지금은 결정적 stub으로 경로와 호출 수만 확인했다.
- 이번 변경을 kind + Helm E2E로 다시 돌리지 않았다. 차트 변경은 vlmStub Deployment와 환경변수 추가뿐이며, lint·template만 확인했다.
- PPTX `convert` 모드는 LibreOffice가 이미지에 없어 동작하지 않는다(`NotImplementedError`).
- PPTX 차트는 렌더링하지 않아 제목·계열 이름만 남는다. XLSX 이미지 경로는 테스트하지 않았다.
- 작은 문서는 `max_tokens`=1024 안에 들어가 청크 1개가 된다. PRD 규칙대로지만, 기본값(1024/128)은 Eval로 조정해야 한다(PRD 열린 질문).
- 청크가 섹션 헤딩으로 시작하면 `contextualized_content`의 경로 접두어와 본문 첫 줄이 같은 제목을 반복한다.
- 작업 중 parse sub-agent가 공유 worktree에서 `git stash`/`pop`을 한 번 실행했다. 다른 변경은 모두 복구됐음을 확인했다.
