---
type: concept
title: XLSX 표 헤더 행 판별 (header_rows)
description: XLSX 표 영역의 헤더 행 수를 파일 메타데이터(엑셀 표·인쇄 제목)와 VLM 텍스트 질의로 정하고 청킹 헤더 반복에 쓰는 구현·검증 기록
tags: [parse, xlsx, table, chunk, vlm, contract]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ../docs/INTERFACES.md
  - ../folio/parse/xlsx.py
  - ../folio/chunk/structure.py
---

# XLSX 표 헤더 행 판별 (header_rows)

## 문제

XLSX 파서(FR-28)는 데이터 영역을 `table` 블록으로 만들지만 헤더 행을 판별하지 않았다. 청킹은 항상 첫 행(과 rowspan으로 묶인 행)을 헤더로 반복했다. 그래서 2단 헤더는 둘째 줄이 본문으로 나뉘었고, 헤더가 없는 표(키-값 목록, 로그)는 첫 데이터 행이 모든 청크에 반복됐다.

## 결정

- 휴리스틱(글꼴·서식·숫자 비율)은 쓰지 않는다. 우선순위는 아래와 같다.
  1. 영역 안의 엑셀 표(ListObject): `headerRowCount`(None이면 1). 엑셀 표 위에 같은 영역으로 붙은 제목 행도 헤더로 센다. 영역 안에 엑셀 표가 여럿이면 가장 위 표를 쓴다.
  2. 시트의 인쇄 제목 행(`print_title_rows`): 영역 첫 행이 그 범위 안에 있을 때만 쓴다.
  3. VLM 텍스트 질의 `table_header`: 2×2 이상 영역에서 앞 15행(셀당 40자)을 보내 `{"header_rows": k}`를 받는다. 답의 마지막 `header_rows` 숫자를 쓰고(사고 텍스트·코드 펜스 허용), `k`가 보낸 행 수 이상이면 버린다.
- 학습형 셀 분류 모델(TabularNet 등)은 공개 한국어 모델이 없어 쓰지 않는다. LLM은 기존 VLM 설정을 재사용해 의존성이 늘지 않는다.
- 계약: `Block.header_rows: int | None`(`cells` 기준, 0 = 헤더 없음, None = 모름 → 기존처럼 첫 행). 지금은 XLSX만 채운다.
- VLM 클라이언트: `call(png, task, text=None)`. 텍스트 질의는 `png=None`. `text=`를 받지 않는 사용자 callable은 요청 없이 건너뛰고 `vlm_fallbacks`에도 남기지 않는다. 요청이 실패하거나 답에 숫자가 없을 때만 fallback으로 기록한다.
- 옵션 `xlsx_header_llm=True`: VLM은 그림 설명에 쓰되 헤더 질의만 끄려면 False로 둔다.
- 청킹: `header_rows=k`이면 행 k-1을 포함하는 rowspan 묶음 끝까지 헤더로 반복한다. `0`이면 반복하지 않고, 첫 조각만 GFM 표 모양(첫 행 + `---`)을 유지한다.

## 실행 결과

- 단위 테스트: `pytest -q` 212 passed, 3 skipped. 추가 테스트는 엑셀 표·인쇄 제목(VLM 호출 0회), 영역별 질의(1×1 영역 제외, 옵션 끔), 응답 파싱(펜스·think, 범위 밖, 숫자 없음 → fallback), 이미지 전용 callable 건너뜀, 청킹 2단 헤더 반복과 `header_rows=0`이다.
- openpyxl 3.1.5에서 엑셀 표(`ref`, `headerRowCount`)와 인쇄 제목(`'$1:$1'`)이 저장 후 `data_only=True` 재로드에서도 읽히는 것을 확인했다.
- 실제 모델: OpenRouter `qwen/qwen3.8-27b`로 합성 시트 6개(단일 헤더, 병합 2단 헤더, 헤더 없는 데이터, 숫자 연도 헤더, 영문, 키-값)를 파싱해 6/6 정답, 요청 6회, fallback 0이었다. 공개 가능한 합성 데이터만 썼다.

## 미완료

- 운영 VLM(Qwen3.5-9B)으로는 아직 확인하지 않았다. 사고 모드를 끄지 않으면 답이 길어질 수 있다.
- 고객사 실제 엑셀 샘플로 정답률을 재지 않았다.
- PDF·PPTX·Markdown 표는 `header_rows`를 채우지 않는다(Markdown GFM 표는 정의상 1행, VLM HTML 표의 `<th>`는 후속 후보).
- 열 헤더(왼쪽 행 머리글)는 다루지 않는다.
- 통합 문서당 질의 수 상한이 없고 요청은 순차 실행이다. 시트·영역이 많으면 그만큼 파싱이 느려진다(운영 VLM은 문서 VLM보다 약 10배 느림, PRD).
