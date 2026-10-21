# M1 벤치마크 공통 계약

- 목적: PRD 열린 질문 3개를 공개 한글 문서(고객사 샘플 대리)로 잠정 결정한다. 결과는 모두 "잠정, 고객사 샘플로 재검증"이다.
  1. 구조 분석 백엔드: Docling vs PP-StructureV3 (MinerU는 라이선스만 확인)
  2. VLM 모델 1개: 문서 전용 VLM vs 범용 VLM — 스캔 OCR·그림 설명·복잡 표 세 작업
  3. `adaptive` 청킹 `max_tokens`·`min_tokens` 기본값 (임베딩 모델 + Eval)
- 실행 위치: DGX Spark (aarch64, GB10, 통합 메모리 128GB, 디스크 여유 ~800GB). 작업 디렉터리 `~/folio-m1-bench/`.
- 공유 자원 규칙
  - 다른 세션의 컨테이너(`embed`, `rerank` 등 vLLM)와 프로세스를 중단·재시작·호출하지 않는다. 우리 컨테이너 이름은 `m1-` 접두사.
  - GPU를 쓰는 측정은 `flock ~/folio-m1-bench/gpu.lock <command>`로 직렬화한다. 시작 전 `nvidia-smi`로 점유를 확인한다.
  - 모델 다운로드는 이름·크기를 결과 문서에 기록한다. 단일 모델 40GB 초과는 하지 않는다.
- 샘플: `~/folio-m1-bench/samples/` (manifest.json 참고). 정답: `samples/truth/`.
- folio 코드: `feat/core-pipeline` 커밋 `753561e`를 `~/folio-m1-bench/folio-src/`에 풀어 사용한다 (`git archive`). 해당 worktree는 읽기만 한다.
- 로컬 소유 범위: 각 sub-agent는 `bench/m1/<자기 영역>/`에만 쓴다. PRD.md·README.md·wiki는 최상위 에이전트만 수정한다.
- 산출물: `bench/m1/<영역>/RESULTS.md` (방법, 표, 결론 후보, 다운로드 목록, 한계). 큰 파일(모델·PDF·예측 결과)은 Git에 넣지 않는다 — Spark에만 둔다.
