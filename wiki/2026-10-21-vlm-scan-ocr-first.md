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
| ocr_first + HTML 표 | 0.799 | 0.886 | 0.882 | 59.2 | 100% |

- 본문은 OCR 수준을 유지하고, 표는 `page`에 가깝고, 시간은 `page`의 61%이며, 단어 위치를 잃지 않는다.
- `page`는 일부 스캔에서 크게 낮았다(s07 0.540, s02 0.656).
- OCR 신뢰도는 0.926~0.971에 몰려 있어 페이지를 고르는 신호가 되지 못했다.

측정 중 찾아 고친 버그:

| 문제 | 상태 |
| --- | --- |
| VLM 그림 설명이 그림 영역 안의 OCR·텍스트 레이어 글자를 덮어씀(s03: 표 글자가 사라짐) | 수정. 원래 글자 뒤에 설명을 붙인다. 회귀 테스트 `test_figure_description_keeps_text_read_in_region` |
| 그림 설명 지시문이 영어라 한글 그림도 영어로 설명 | 지시문 수정(그림 안 글자의 언어로). 실제 출력 언어는 아직 확인하지 않음 |

HTML 표(후속, 같은 날): VLM 표·페이지 지시문을 HTML 표(rowspan/colspan)로 바꾸고, PP 어댑터에 있던 HTML 표 파서를 `folio/parse/html_table.py`로 옮겨 VLM 결과와 함께 쓰게 했다. `md_blocks`는 Markdown 안의 HTML 표(```html 울타리, 표 안 빈 줄 포함)를 `spans`가 있는 표 블록으로 읽는다. 파이프 표는 그대로 읽는다. `ocr_first` 표 평균 0.846→0.882, 병합 셀 표 t03·t04는 1.0, VLM 표 요청 시간은 늘었다(전체 45→59초/장). 상세는 [RESULTS.md](../bench/m1/vlm_scan/RESULTS.md)의 "HTML 표 적용 후"에 있다.

검증: 단위 테스트 140개 통과(`ocr_first` 경로 테스트 3개 추가). pgx 측정은 위 표.

## 미완료

- t08(실제 표)이 어느 정책에서도 0.5 안팎인 원인.
- x86 GPU 장비에서 PP GPU로 속도 재측정.

### 기술부채 (사용자 결정 2026-10-21: 보류)

- **PP-StructureV3 메모리 급증.** 원인: 글자 검출(PP-OCRv5_server_det)이 거의 원본 해상도로 돌아 1~2초 동안 메가픽셀당 약 5.5GB를 쓴다(누수 아님). 측정 RSS: 디지털 페이지(약 2MP) 12.7GB, 스캔(약 3.9MP) 22GB, 5119x6667 스캔 r03은 47GB에서 감시 스크립트가 중단(외삽 약 66GB, M1 샤드3의 66GB와 일치). 해결 후보: `text_det_limit_type="max"`, `text_det_limit_side_len=1600` → r03 12.2GB·60초, s04·s07 22GB→12.2GB 이하, 출력 글자 수 -1.6%·-5%. OCR 정확도 영향은 미측정. 조사 기록: `bench/m1/backends/mem/NOTES.md`(브랜치 `fix/pp-memory`, 미병합).
- **`ocr_min_conf` 동작 확인.** 이번 샘플은 OCR 신뢰도가 0.926~0.971에 몰려 기준값이 한 번도 작동하지 않았다. 저품질 실제 스캔에서 확인 필요.
- **그림 설명의 검색 효과.** 지표상 손해(s09 0.686→0.338)이지만 검색 품질 영향은 미측정.
