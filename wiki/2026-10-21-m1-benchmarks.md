---
type: concept
title: M1 벤치마크 — 구조 분석 백엔드·VLM·adaptive 청킹 (공개 한글 샘플)
description: 고객사 샘플 대신 공개 한글 문서로 구조 분석 백엔드, VLM 모델 1개, adaptive 청킹 기본값을 비교한 작업 기록과 잠정 결론
tags: [benchmark, m1, structure-backend, docling, ppstructure, vlm, chunking, mineru]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ../bench/m1/BRIEF.md
  - ../bench/m1/samples/README.md
  - ../bench/m1/backends/RESULTS.md
  - ../bench/m1/vlm/RESULTS.md
  - ../bench/m1/chunking/RESULTS.md
  - ./2026-10-21-ppstructure-gpu-default.md
  - https://github.com/opendatalab/MinerU/blob/master/LICENSE.md
---

# M1 벤치마크 — 구조 분석 백엔드·VLM·adaptive 청킹

PRD 열린 질문 세 개를 공개 한글 문서로 미리 확인했다. 고객사 샘플이 없어서 모든 결론은 잠정이고, 첫 고객사 샘플로 다시 확인해야 한다.

## 결정

- 사용자 결정(2026-10-21): 고객사 샘플이 없으므로 공개 한글 문서로 대리 측정하고 결과는 잠정으로 기록한다.
- 사용자 결정(2026-10-21): HWPX 변환기 후보에서 EWL 방식을 언급 없이 제외한다(`docs/hwpx-converter-bench` 브랜치에서 반영).
- 기존 사용자 결정(2026-10-20)은 그대로 둔다: 기본 구조 분석 백엔드는 PP-StructureV3 GPU([기록](./2026-10-21-ppstructure-gpu-default.md)). 이 문서의 결과는 재확인을 위한 근거이고 기본값을 바꾸지 않는다.
- 청킹 기본값(1024/128)과 VLM 모델은 아직 바꾸지 않았다. 사용자 결정이 필요하다.

## 가정

- 공개 문서(금융위원회 보도자료, 통계청 보고서, 공정거래위원회 표준약관)가 고객사 문서를 대신할 수 있다고 본다. 법령 원문 PDF와 현대 한글 실제 스캔은 없다.
- 스캔 OCR 점수는 디지털 페이지를 열화시킨 합성 스캔 10장으로 낸다. 실제 1948년 스캔 4장은 정답이 없어 정성 확인만 했다.
- 청킹 평가는 "검색해 오는 토큰 예산이 고정"이라는 전제다. 8k 이상을 넣는 환경이면 더 큰 청크가 나을 수 있다.

## 실행 결과

실행 위치: DGX Spark(`pgx`, aarch64, GB10, 통합 메모리 128GB). 원시 결과는 pgx `~/folio-m1-bench/out/`에만 있고 Git에 넣지 않았다.

### 샘플 세트

문서 50개, 345쪽. 디지털 16, 스캔 14(합성 10, 실제 4), 복잡 표 10(합성 6, 실제 약관 4), 그림 10. 생성 스크립트와 출처는 [샘플 README](../bench/m1/samples/README.md).

### MinerU 라이선스

PyPI 4.0.12(2026-10-11) 기준 Apache-2.0에 추가 조건이 붙는다. 계열사 합산 MAU 1억 또는 월 매출 2천만 달러를 넘으면 별도 상용 라이선스가 필요하고, 온라인 서비스로 제공하면 MinerU 사용을 표기해야 하며, 어기면 라이선스가 자동 종료된다. 대형 고객사는 매출 조건에 걸릴 수 있어 기본 후보에서 뺐다.

### 구조 분석 백엔드 (Docling vs PP-StructureV3)

상세: [backends/RESULTS.md](../bench/m1/backends/RESULTS.md).

- pgx(aarch64, sm_121)에서 Docling은 GPU로 돈다(torch 2.14.1+cu130). PP-StructureV3는 paddlepaddle-gpu 3.3.1(cu129)·3.5 nightly(cu130) 모두 `run_check()`에서 "Unsupported GPU architecture"로 실패해 CPU로만 쟀다.
- 첫 측정에서 PP-StructureV3는 어댑터 버그 두 개(아래) 때문에 116쪽이 실패하고 실제 표가 뒤집혔다. 두 버그를 고친 뒤 다시 쟀다.
- 잠정 권고는 Docling이다(확신도 중간). 기존 기본값(PP-StructureV3 GPU)은 바꾸지 않았다.

| 지표 (버그 수정 후) | Docling | PP-StructureV3 (CPU) |
| --- | --- | --- |
| 디지털 텍스트 일치 | 0.9997 | 0.9997 |
| 스캔 OCR 유사도 (합성 10) | 0.63 | **0.816** |
| 표 셀 정확도 10개 평균 | **0.843** | 0.735 |
| 합성 표 t01~t06 | 0.86~1.0 | **1.0** |
| 실제 약관 표 t07~t10 평균 | **0.67** | 0.34 |
| 쪽 단위 실패 | 0 | 0 |
| CPU s/page (부하 없을 때 재측정, 디지털 / 스캔 / 표) | **1.07 / 3.22 / 1.55** | 18.95 / 29.1 / 10.2 |
| 최대 메모리 | 약 5.5 GB | 샤드당 12 GB, 한 샤드 66 GB (원인 미확인) |

- Docling GPU(GB10) 전체 실행 중앙값은 디지털 0.47, 스캔 3.15, 표 0.16 s/page다(다른 세션 vLLM과 GPU 공유 중). PP-StructureV3 GPU 속도는 이 장비에서 잴 수 없었다.
- 한글 스캔 OCR이 중요하면 PP-StructureV3가 낫다. Docling 구조 + PP OCR 조합은 시험하지 않았다.

### VLM 모델 1개

상세: [vlm/RESULTS.md](../bench/m1/vlm/RESULTS.md). 잠정 선택은 Qwen3.5-9B다(확신도 중간).

| 모델 | 스캔 OCR 유사도 / CER | 표 셀 정확도 | 그림 사실 재현율 | 스캔 s/page |
| --- | --- | --- | --- | --- |
| Qwen3.5-9B | 0.895 / 0.228 | 0.93 (HTML 지시) | 0.90 | 78 |
| Qwen3-VL-8B-Instruct | 0.865 / 0.196 | 0.81 (HTML 지시) | 0.77 | 80 |
| GLM-OCR | 0.811 / 0.301 | 0.79 | 0.73 | 11.7 |
| PaddleOCR-VL-1.6 | 0.836 / 0.383 | 0.75 | 0.62 | 8.3 |

- 그림 설명은 후보와 다른 계열인 gemma-4-12B-it가 1회 채점했다. 오차 범위는 모른다.
- 병합 셀 표는 HTML 지시에서만 정확하다. 파이프라인의 `markdown_to_blocks`는 파이프 표만 읽으므로 HTML 표 파서가 필요하다.
- GLM-OCR에 "한국에서는 권리가 없다"는 웹 글이 하나 있었다. 모델 카드는 MIT이고 제한 조항은 찾지 못했다. 법무 확인 전이다.
- 모델 다운로드 약 63GB, pgx `~/folio-m1-bench/models/`.

### adaptive 청킹 기본값

상세: [chunking/RESULTS.md](../bench/m1/chunking/RESULTS.md). 잠정 최적은 `max_tokens=512`, `min_tokens=64`다(확신도 중간 이하).

| 설정 | 청크 수 | recall@1k | recall@2k | recall@4k |
| --- | --- | --- | --- | --- |
| adaptive 512/64 | 1091 | 0.691 | 0.793 | 0.873 |
| adaptive 768/128 | 679 | 0.649 | 0.797 | 0.856 |
| adaptive 1024/128 (현재 기본값) | 559 | 0.645 | 0.776 | 0.848 |
| adaptive 2048/128 | 410 | 0.550 | 0.693 | 0.805 |
| fixed 512 | 691 | 0.595 | 0.718 | 0.802 |

- 질의 249개(Qwen3.8-27B 생성, 정답은 원문 글자 구간), bge-m3 dense 검색, 같은 검색 토큰 예산 비교. 조합은 두 단계로 줄여 돌렸다.
- `max_tokens` 256~1024는 2k 예산에서 통계적으로 구분되지 않는다. 1536·2048은 확실히 나쁘다. `adaptive`는 같은 크기의 `fixed`보다 확실히 좋다.
- 손 검수 20개(최종 249개로 줄이기 전 343개에서 추출): 통과 14, 결함 3, 모호 3.

### 측정 중 발견한 folio 문제

| 문제 | 상태 |
| --- | --- |
| `folio/backends/ppstructure.py`가 paddle의 numpy 배열(`cell_box_list`)에 `or []`를 써서 표가 있는 페이지에서 `ValueError` | pgx에서 재현 후 수정, 회귀 테스트 추가(`tests/test_backends.py::test_ppstructure_numpy_results`). 같은 패턴의 `rec_scores`도 수정 |
| `ppstructure.py`가 `cell_box_list`를 `<td>` 순서와 그대로 짝지어, 상자가 오른쪽 아래부터 오는 실제 표가 행·열 모두 뒤집힘 | 수정(`_reading_order`), 회귀 테스트 `test_ppstructure_cell_reading_order`. 실제 표 셀 정확도 0.0~0.12 → 0.28~0.42. 상자 수와 `<td>` 수가 다르면 여전히 어긋남 |
| 표 헤더 행 하나가 `max_tokens`보다 길면 `adaptive`가 상한을 넘는 중복 청크를 수천 개 만든다(d01 31쪽, Docling의 139열 표) | 수정(`folio/chunk/strategies.py`): 헤더가 `max_tokens`의 절반을 넘으면 조각마다 반복하지 않고 첫 행들처럼 나눈다. 회귀 테스트 `test_table_header_over_max_split_once_not_repeated`. pgx에서 d01(표 포함) 512/64 재청킹: 수정 전 6143개 중 5971개 상한 초과 → 수정 후 187개, 초과 0. 벤치마크 수치는 표를 뺀 채로 낸 것이라 그대로다 |
| 약관 d12·d13의 `제N조` 줄이 헤딩으로 인식되지 않아 청킹이 페이지 단위로 내려간다 | 수정(`folio/parse/assemble.py` `apply_sections`): 문단이 `제N조(제목)`로 시작하면 현재 헤딩 아래 조 섹션을 연다. 조 표시만 있는 문단은 헤딩이 되고, 제목 뒤에 본문이 이어지면 문단의 `section_path`에 조를 넣는다. `제3조에 따라`처럼 본문 속 참조는 제외. 회귀 테스트 `test_article_paragraphs_become_sections`. pgx에서 기존 파싱 결과에 적용: d12 512/64 청크 24개 중 22개, d13 37개 중 34개가 조 경로를 가진다(수정 전 0). 검색 성능 재측정은 안 했다 |

## 미완료

- 첫 고객사 샘플로 세 결론을 다시 확인한다.
- 청킹 기본값을 512/64로 바꿀지, VLM을 Qwen3.5-9B로 정할지 사용자 결정.
- VLM 출력용 HTML 표 파서, Qwen3.5-9B FP8 서빙 속도 확인.
- `제N조` 섹션 적용 후 청킹 Eval 재측정(d12·d13).
- ARM GPU 호스트에서 PP-StructureV3 GPU를 쓸 방법(소스 빌드 등) 또는 Docling 기본 여부.
- `bench/m1-models` 브랜치의 dev 통합.
