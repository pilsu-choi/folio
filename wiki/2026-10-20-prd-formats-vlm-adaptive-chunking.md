---
type: concept
title: PRD에 입력 형식 확장, VLM 기본 적용, adaptive 청킹 추가
description: PPTX·XLSX·Markdown·TXT·이미지 파싱, 이미지 콘텐츠 VLM 기본 적용, 구조 기반 adaptive 청킹을 PRD에 반영한 작업 기록
tags: [prd, parse, vlm, chunk, pptx, xlsx, markdown]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ./2026-10-20-prd-container-deployment.md
---

# PRD에 입력 형식 확장, VLM 기본 적용, adaptive 청킹 추가

## 작업 내역

[PRD.md](../PRD.md)에 다음을 반영했다.

- 목표 2 수정(디지털 텍스트는 OCR·VLM 없이, 이미지 콘텐츠는 VLM 기본), 목표 7 추가(구조 기반 청킹)
- FR-02·FR-05·FR-06 수정: 스캔 페이지·이미지 파일·그림·복잡 표는 VLM 기본, `parse.vlm: false`이면 구조 분석 OCR. FR-06 우선순위 P2 → P1
- FR-27(PPTX native), FR-28(XLSX native), FR-29(Markdown·TXT native) 추가, 모두 P1
- FR-07 교체: `auto`(섹션·표·페이지·고정 중 선택) → `adaptive`(구조 트리에서 `max_tokens` 안의 가장 큰 의미 단위). 대안은 `page`·`fixed`. chunk 단계 출력 계약 검증 추가
- Document JSON: `code` 블록 타입 추가, `width`·`height`·`bbox`를 페이지형 입력에서만 필수로 변경, `figure.text`에 VLM 설명
- Chunk Record: 여러 페이지에 걸친 청크용 선택 필드 `pages` 추가
- `### 입력 형식별 경로` 표, `## 청킹` 절 신설
- 이미지(`folio-vlm` 기본), Compose GPU 없는 profile, 비기능 성능, 레포 구조, 기술 스택, fixtures, 열린 질문 갱신

## 결정과 가정

- 결정(사용자): VLM은 이미지 콘텐츠(이미지 파일, 스캔 페이지, 그림·차트, 복잡 표)에만 기본 적용한다. 디지털 텍스트 경로는 그대로 두어 문제 2(느린 Parse)와 FR-01의 OCR 0회 기준을 유지한다.
- 결정(제안): `auto`와 `section`·`table` 전략은 `adaptive`와 같은 역할이므로 따로 두지 않고 `adaptive`로 합쳤다.
- 결정(제안): PPTX·XLSX는 PDF 변환 대신 `native`를 기본으로 한다. PPTX는 `convert` 옵션을 둔다.
- 가정: `max_tokens` 1024, `min_tokens` 128은 임시값이며 Eval로 확정한다. 라이브러리(python-pptx, openpyxl, markdown-it-py, charset-normalizer) 선택과 라이선스는 확인하지 않았다.

## 검증

- 문서만 변경했다. 코드가 없으므로 수용 기준은 검증하지 않았다.
- 원격 저장소가 없어 로컬 `dev`에서 브랜치 `prd/formats-adaptive-chunk`와 worktree `../folio-prd-formats`를 만들어 작업했다.
- README.md는 비어 있고 구현된 기능이 바뀌지 않아 수정하지 않았다.

## 미완료

- 마일스톤 표(임베디드 콘텐츠)에 새 FR 배치
- VLM 모델 선정(스캔 파싱 + 그림 설명을 모델 하나로 할지)
