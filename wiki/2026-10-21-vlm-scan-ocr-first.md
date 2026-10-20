---
type: concept
title: 스캔 페이지 VLM 정책 — OCR 먼저(ocr_first)를 기본으로
description: 스캔·이미지 페이지를 페이지 전체 VLM 대신 OCR 먼저 처리하고 표·그림만 VLM에 보내는 정책을 구현하고 공개 한글 샘플로 비교한 기록
tags: [parse, vlm, ocr, scan, benchmark, m1]
status: draft
generated: 2026-10-21
sources:
  - ../bench/m1/vlm_scan/RESULTS.md
  - ../PRD.md
  - ./2026-10-21-m1-benchmarks.md
---

# 스캔 페이지 VLM 정책 — OCR 먼저(ocr_first)

## 결정

- 사용자 결정(2026-10-21): 스캔 페이지도 OCR을 먼저 돌리고 필요한 부분만 VLM을 쓰는 방식으로 진행한다.
- 측정 결과에 따라 `parse` 옵션 `vlm_scan`의 기본값을 `ocr_first`로 했다. 기존 동작은 `vlm_scan: page`로 쓸 수 있다.
- `ocr_min_conf` 기본값 0.85는 잠정값이다. 이번 샘플에서는 신호가 없어 근거가 약하다(아래).

## 동작

- `ocr_first`(기본): 구조 분석 백엔드로 OCR → 단어 bbox를 그대로 둔 블록 생성 → 표 블록은 잘라서 VLM으로 다시 읽고, 그림 블록은 VLM 설명을 덧붙인다. OCR 신뢰도(글자 수 가중 평균)가 `ocr_min_conf` 미만이거나 글자가 하나도 없으면 페이지 전체를 VLM으로 보낸다. OCR이 실패해도 VLM 페이지로 넘어간다.
- `page`: 기존처럼 페이지 전체를 VLM에 보낸다. 모든 블록의 bbox가 페이지 전체다.
- VLM이 꺼져 있으면 두 값 모두 OCR만 쓴다(기존과 같음).
- 코드: `folio/parse/core.py` `_image_path`, `_vlm_regions`(디지털 페이지의 그림·약한 표 보강과 공유).

## 가정

- 합성 스캔 10장과 열화 없는 표 이미지 10개가 고객사 스캔을 대신한다고 본다. 실제 저품질 스캔은 정답이 없어 쓰지 않았다.
- OCR은 PP-StructureV3 CPU다. GPU OCR이면 `ocr_first`의 시간 이득이 더 커질 것으로 보지만 재지 않았다.

## 실행 결과

pgx에서 측정. 상세 표와 명령은 [RESULTS.md](../bench/m1/vlm_scan/RESULTS.md)에 있다.

| 정책 | 스캔 유사도 | 그림 블록 제외 | 표 셀 정확도 | 평균 s/page | 실제 bbox |
| --- | --- | --- | --- | --- | --- |
| OCR만 | 0.864 | 0.888 | 0.691 | 26.5 | 100% |
| page (기존) | 0.831 | - | 0.867 | 74.0 | 0% |
| ocr_first | 0.796 | 0.882 | 0.846 | 45.2 | 100% |

- 본문은 OCR 수준을 유지하고, 표는 `page`에 가깝고, 시간은 `page`의 61%이며, 단어 위치를 잃지 않는다.
- `page`는 일부 스캔에서 크게 낮았다(s07 0.540, s02 0.656).
- OCR 신뢰도는 0.926~0.971에 몰려 있어 페이지를 고르는 신호가 되지 못했다.

측정 중 찾아 고친 버그:

| 문제 | 상태 |
| --- | --- |
| VLM 그림 설명이 그림 영역 안의 OCR·텍스트 레이어 글자를 덮어씀(s03: 표 글자가 사라짐) | 수정. 원래 글자 뒤에 설명을 붙인다. 회귀 테스트 `test_figure_description_keeps_text_read_in_region` |
| 그림 설명 지시문이 영어라 한글 그림도 영어로 설명 | 지시문 수정(그림 안 글자의 언어로). 실제 출력 언어는 아직 확인하지 않음 |

검증: 단위 테스트 140개 통과(`ocr_first` 경로 테스트 3개 추가). pgx 측정은 위 표.

## 미완료

- VLM 표를 HTML로 받는 파서. 병합 셀 표 t04가 `ocr_first`에서 0.667이다.
- 저품질 실제 스캔에서 `ocr_min_conf`가 페이지 전체 VLM으로 넘기는지 확인.
- 그림 설명이 검색 품질에 주는 효과 측정.
- x86 GPU 장비에서 PP GPU로 속도 재측정.
