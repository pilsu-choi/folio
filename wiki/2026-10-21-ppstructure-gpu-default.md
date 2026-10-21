---
type: concept
title: PP-StructureV3를 기본 구조 분석 백엔드로 (GPU)
description: 기본 구조 분석 백엔드를 PP-StructureV3 GPU로 바꾼 작업, GPU·CPU 측정, aarch64 import 순서 문제 기록
tags: [ppstructure, gpu, cpu, structure-backend, deploy, benchmark]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ./2026-10-20-implementation-core-pipeline.md
---

# PP-StructureV3를 기본 구조 분석 백엔드로 (GPU)

## 결정

- 사용자 결정(2026-10-20): 기본 구조 분석 모델은 PP-StructureV3, 기본 실행은 GPU. 한글이 들어간 고객사 샘플로 다시 확인하는 일은 남아 있다.
- 사용자 결정(2026-10-20): 고객사 환경에 따라 변형을 고른다. 폐쇄망 번들 기본값은 `STRUCTURE_VARIANTS=gpu`. 번들 빌드·설치 테스트는 시간이 오래 걸려 당분간 보류한다.

## 구현

| 항목 | 내용 |
| --- | --- |
| 구조 분석 서버 | `FOLIO_STRUCTURE_BACKEND`·`FOLIO_STRUCTURE_DEVICE`·`FOLIO_STRUCTURE_CPU_THREADS`로 백엔드를 만든다. 가중치는 백그라운드에서 로드하고, 끝나기 전 `/readyz`는 503(`loading`·`error`)을 돌려준다 |
| 백엔드 | ppstructure·docling이 `device`(gpu → `gpu:0`·`cuda`), `cpu_threads`, `warmup()`을 받는다 |
| 이미지 | `Dockerfile.structure` 기본값 `BACKEND=ppstructure DEVICE=gpu`. paddlepaddle(-gpu) 3.3.1(cu126), paddleocr 3.7 |
| Compose | 프로필 `gpu`(기본)·`cpu`·`dev`(heuristic). 셋 다 네트워크 alias `structure`를 쓴다. `docker-compose.gpu.yml`은 삭제했다. `cpu`는 `cpus` 4, 스레드 4, `mem_limit` 14g |
| Helm | 기본값 ppstructure + GPU. `values-cpu.yaml`(GPU 끔, 스레드 4, 요청 12Gi·한도 14Gi), `values-dev.yaml`은 heuristic |
| 번들·CI·kind | 번들은 `STRUCTURE_VARIANTS`(기본 `gpu`). kind E2E와 CI는 `-heuristic` 이미지 |

## 측정 (로컬 RTX 4070, 2026-10-20, 실제 실행)

- 이미지 `folio-structure:0.1.0-gpu`: 8.05 GB (paddlepaddle-gpu 3.3.1 cu126 + paddleocr 3.7).
- 모델 로드(warmup, 가중치는 미리 채운 볼륨 사용): 18 s. 로드 후 GPU 메모리 약 +1.2 GB.
- 미리 로드한 상태에서 HTTP 서버로 픽스처 6개(11페이지)를 parse했다(구조 분석 8회, OCR 3회). 페이지당 **0.42 s**.
- `scripts/bench_backends.py`(http → GPU 서버) 결과: 디지털 텍스트 1.000, 표 일치, OCR 유사도 0.92, 스키마 5/5.
- 2026-10-21 같은 스크립트(`warm_bench.py`: warmup → digital.pdf로 한 번 예열 → 픽스처 6개 parse)로 다시 쟀다. 컨테이너 제한은 `--cpus 4 --memory 6g`이었다. 결과는 **0.33 s/page**, 컨테이너 최대 메모리 3.6 GiB, warmup 12.4 s.

## 측정: CPU (DGX Spark aarch64, 2026-10-21, 실제 실행)

- 컨테이너 제한: `--cpus 4 --memory 16g`, `cpu_threads=4`, `OMP_NUM_THREADS=4`. 이미지는 DGX Spark에서 arm64로 빌드했다(1.79 GB, paddlepaddle 3.3.1 CPU).
- 측정 조건과 대상(픽스처 11페이지, 구조 분석 8회, OCR 3회)은 GPU와 같다. 결과는 다음과 같다.
  - **17.6 s/page**. GPU의 약 40~50배다(GPU 0.33~0.42 s/page 기준).
  - 최대 메모리 11.55 GiB(cgroup `memory.peak`).
  - warmup 2.7 s.
- `--memory 8g`로 돌리면 모든 모델을 로드한 뒤 첫 추론에서 OOM(exit 137)이 났다. 그래서 Helm `values-cpu.yaml`은 요청 12Gi·한도 14Gi, Compose `cpu`는 `mem_limit` 14g로 정했다.
- ARM 수치다. x86 CPU 수치는 재지 않았다.
- GPU 결과와 비교했다.
  - 6개 파일 모두 블록 수·블록 타입 순서·표 셀이 같았다.
  - 텍스트 유사도는 디지털 4개 파일이 1.000, scanned.pdf 0.993, mixed.pdf 0.997이다. OCR 텍스트만 조금 다르다.

## 알려진 문제: aarch64에서 import 순서

- 증상: DGX Spark(aarch64) CPU에서 pymupdf(`fitz`)를 paddle보다 먼저 import하면 PP-StructureV3 첫 모델 생성에서 segfault가 난다. 위치는 `AnalysisPredictor::SaveOrLoadPirParameters`의 `std::filesystem::path` 소멸자다.
- 재현 결과(같은 컨테이너, 첫 import만 바꿈):

  | 먼저 import | 결과 |
  | --- | --- |
  | `fitz` | segfault |
  | `folio.parse` | segfault |
  | `paddle` 다음 `folio.parse` | 정상 |

  `enable_mkldnn`·`cpu_threads`·PIR 플래그는 결과와 상관이 없었다.
- 영향:
  - 구조 분석 서버 경로(`folio structure-server`)는 pymupdf를 import하지 않음을 확인했다. 배포(Compose·Helm)는 영향이 없다.
  - aarch64에서 ppstructure를 프로세스 안에서 parse와 함께 쓰려면 paddle을 먼저 import해야 한다.
  - x86에서는 이전 프로세스 내 벤치마크가 정상 동작했다.
- 원인은 두 라이브러리의 C++ 런타임 충돌로 추정하며, 확인하지는 않았다.

## 사고 기록

- 로컬(WSL, 메모리 15 GB)에서 PP-StructureV3 CPU를 프로세스 안에서 돌리다가 PC가 멈췄다. 코어와 메모리 제한 없이 돌렸고, 동시에 GPU 컨테이너가 떠 있었으며 pip 설치도 진행 중이었다.
- 이후 무거운 작업은 자원 제한(`--cpus`, `--memory`, 스레드 수)을 건 컨테이너에서 하나씩 실행한다. AGENTS.md에 따라 DGX Spark에서 실행한다.

## 미완료·미검증

- 번들 빌드·설치 테스트: 보류(사용자 지시).
- GPU 프로필 Compose E2E 전체와 Helm GPU 실제 설치: 하지 않았다. GPU 서버 단독 실행, `helm lint`·`template`(default·dev·cpu·gpu), `docker compose config`만 확인했다.
- x86 CPU 이미지 실측: 하지 않았다.
- 고객사 한글 샘플로 재확인: 하지 않았다.

## 정리한 자원

- DGX Spark: `~/folio-ppcpu-test`와 `folio-structure:ppcpu-test` 이미지(작업 후 삭제).
- 로컬: `folio-gpu-test` 컨테이너.
- 남김: 로컬 `folio-structure:0.1.0-gpu` 이미지와 `folio-gpu-models` 볼륨은 재측정용으로 남겼다. `0.1.0-cpu`와 `search-e2e-cpu` 태그는 다른 세션 것일 수 있어 건드리지 않았다.
