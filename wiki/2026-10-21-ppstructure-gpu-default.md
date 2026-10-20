---
type: concept
title: PP-StructureV3를 기본 구조 분석 백엔드로 (GPU)
description: 기본 구조 분석 백엔드를 PP-StructureV3 GPU로 바꾼 작업과 측정 기록 (진행 중)
tags: [ppstructure, gpu, structure-backend, deploy]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ./2026-10-20-implementation-core-pipeline.md
---

# PP-StructureV3를 기본 구조 분석 백엔드로 (GPU)

## 결정

- 사용자 결정(2026-10-20): 기본 구조 분석 모델은 PP-StructureV3, 기본 실행은 GPU. 한글이 들어간 고객사 샘플로 다시 확인하는 일은 남아 있다.

## 측정 (로컬 RTX 4070, 2026-10-20, 실제 실행)

- 이미지 `folio-structure:0.1.0-gpu`: 8.05 GB (paddlepaddle-gpu 3.3.1 cu126 + paddleocr 3.7).
- 모델 로드(warmup, 가중치는 미리 채운 볼륨 사용): 18 s. 로드 후 GPU 메모리 약 +1.2 GB.
- 미리 로드한 상태에서 HTTP 서버로 픽스처 6개(11페이지)를 parse했다(구조 분석 8회, OCR 3회). 페이지당 **0.42 s**.
- `scripts/bench_backends.py`(http → GPU 서버) 결과: 디지털 텍스트 1.000, 표 일치, OCR 유사도 0.92, 스키마 5/5.
- CPU와 공정하게 비교한 측정(모델 로드 제외)은 아직 없다. 이전 CPU 수치 20.6 s/page는 모델 로드를 포함한 값이다.

## 사고 기록

- 로컬(WSL, 메모리 15 GB)에서 PP-StructureV3 CPU를 프로세스 안에서 돌리다가 PC가 멈췄다. 코어와 메모리 제한 없이 돌렸고, 동시에 GPU 컨테이너가 떠 있었으며 pip 설치도 진행 중이었다.
- 이후 무거운 작업은 자원 제한(`--cpus`, `--memory`, 스레드 수)을 건 컨테이너에서 하나씩 실행한다. AGENTS.md에 따라 pgx에서 실행한다.
