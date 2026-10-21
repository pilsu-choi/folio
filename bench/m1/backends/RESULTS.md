# M1 구조 분석 백엔드: Docling vs PP-StructureV3 (잠정, 고객사 샘플로 재검증 필요)

## 요약
- 표(table) 품질: 합성 표는 PP-StructureV3가 6/6 완벽(cell_acc 1.0), Docling은 0.86~1.0. 그러나 실제 약관 표(t07~t10, 재렌더)는 PP-StructureV3 cell_acc가 0.0~0.12로 붕괴하고 Docling이 0.38~0.92로 우세. 전체 평균 Docling 0.843 vs PP 0.635.
- 스캔 한글 OCR: PP-StructureV3(PP-OCRv5 korean) 평균 유사도 0.816 vs Docling(RapidOCR) 0.63. PP 우세.
- 디지털 text match: 두 백엔드 모두 0.9997 (텍스트 레이어 기반이라 변별력 없음).
- **PP-StructureV3는 GB10에서 GPU 불가(CPU 전용), 속도는 Docling 대비 약 10~20배 느림, 메모리 12GB 이상(최대 66GB 관측).** 또한 어댑터 버그로 디지털 116쪽에서 구조 분석이 실패(아래).
- 권고 기본값: **Docling** (신뢰도 중간). 스캔 한글 OCR 품질이 핵심인 코퍼스에서는 PP-StructureV3 OCR이 우세하므로 조건부 대안.

## GPU 가능성 (Step 1)
| 백엔드 | 결과 |
|---|---|
| Docling | torch 2.14.1+cu130 (aarch64 wheel, download.pytorch.org/whl/cu130)가 GB10(sm_121)에서 동작(`cuda.is_available()`, matmul 확인). GPU 실행 성공. 단, OCR(RapidOCR, onnxruntime)은 CPU. |
| PP-StructureV3 | paddlepaddle-gpu 3.3.1(stable cu129 aarch64 wheel)과 3.5.0.dev20260915(nightly cu130 aarch64 wheel) 모두 `paddle.utils.run_check()`에서 `RuntimeError: Unsupported GPU architecture` (CUDA_FORCE_PTX_JIT=1도 무효). 소스 빌드(sm_121)·NVIDIA 컨테이너는 시간 제한으로 시도하지 않음(Baidu 레지스트리 접속도 타임아웃). **CPU 전용으로 측정.** |

비대칭: Docling은 GPU와 CPU 둘 다 측정. 같은 CPU 조건의 속도 비교는 아래 "재측정" 표만 유효.

## 방법
- 하니스 재사용: `folio-src/scripts/bench_backends.py`의 `text_match`, `table_scores`, `norm`과 OCR 유사도 공식(difflib), `folio.parse.default(backend=...)`. 새 하니스 없이 `run_manifest.py`(얇은 어댑터)가 `samples/manifest.json`을 순회.
- 설정(어댑터 기본값 그대로): Docling `table_mode=accurate`, RapidOCR korean, `device=cuda|cpu`. PP `lang=korean`, 어댑터 기본 옵션(방향분류/왜곡보정/수식/차트/인장 off, 표 인식 on, mkldnn off), `device=cpu`. 변경한 옵션: 장치 지정, 샤드 실행 시 PP `cpu_threads=4`(아래).
- 샘플: digital 16문서(311쪽), scan 14(합성 10 = 정답 있음, 실제 1948년 스캔 r01~r04 = 정답 없음, 시간/실패만), table 10(합성 6 + 실제 재렌더 4). figure 10은 구조 백엔드 대상 아님.
- 웜업(모델 다운로드/로딩)은 시간에서 제외(docling 13~18초, PP 60~300초).
- digital: PyMuPDF 텍스트 레이어 대비 단어 재현율(전체 문서). 텍스트는 텍스트 레이어에서 오므로 거의 1.0이며 구조 백엔드 변별력이 낮다.
- 피크 메모리: 프로세스 RSS 최대치(`ru_maxrss`/`/usr/bin/time -v`). GB10은 통합 메모리라 nvidia-smi 값 N/A.
- 환경: DGX Spark(GB10, 드라이버 580.178, CUDA 13.0, aarch64, 20코어), Python 3.12, docling(venv-docling, torch 2.14.1+cu130), paddlepaddle-gpu 3.3.1(cu129 빌드, 실제로는 CPU 경로) + paddleocr[doc-parser]. GPU 측정은 `flock ~/folio-m1-bench/gpu.lock`, 다른 세션의 컨테이너(embed, rerank)는 건드리지 않음(단, 그 컨테이너들이 GPU를 점유 중이라 GPU 시간에는 경합 영향 가능).

## 품질 결과 (전체 샘플, 장치 무관)
| 항목 | Docling (GPU = CPU와 동일) | PP-StructureV3 (CPU) |
|---|---|---|
| digital text match (평균/최소) | 0.9997 / 0.9955 | 0.9997 / 0.9955 |
| scan OCR 유사도 (합성 10, 평균 / 최소) | 0.629(GPU) 0.632(CPU) / 0.303 | **0.816** / 0.447 |
| table rows_ok / cols_ok / spans_ok (/10) | 8 / 10 / 6 | 9 / 10 / 6 |
| table cell_acc 평균 | **0.843** | 0.635 |
| table cell_acc 합성 t01~t06 | 1.0,0.89,1.0,1.0,1.0,0.86 | **1.0 x6** |
| table cell_acc 실제 t07~t10 | 0.92,0.38,0.78,0.61 | 0.12,0.0,0.11,0.11 |
| 실패(예외로 파일 전체 실패) | 0 | 0 |
| 쪽 단위 구조 분석 실패 | 0 | **116쪽** (d09 62/81, d11 46/46, d10 5/42, d13 1, d14 2): `ValueError: The truth value of an array with more than one element is ambiguous` |

(수정 전 결과) PP 쪽 단위 실패는 `folio/backends/ppstructure.py`가 numpy 배열에 `or`(`t.get("cell_box_list") or []` 등)을 쓰는 경로로 추정(미확인, 해당 코드는 읽기 전용이라 수정하지 않음). 실패한 쪽은 구조(표/영역) 없이 처리되므로 PP의 digital/표 결과는 이 버그 수정 시 달라질 수 있다. 실제 표(t07~t10) cell_acc 붕괴가 같은 원인인지 셀 매핑 문제인지도 미분리.

실제 스캔 r01~r04(정답 없음): 출력 문자 수 Docling 436/354~359/353~372/2079~2082, PP 444/452/1512/3714. PP가 더 많이 읽음(1948년 국한문혼용 신문, 정성 검증 필요).

## 속도 (sec/page)
**기존 전체 실행의 CPU 타이밍은 오염됨**: ppstructure_cpu 실행 중 다른 세션(chunking 에이전트 parse_docs2.py, Docling CPU)이 동시에 CPU를 사용했고, 이후 4샤드 병렬(샤드당 4스레드)로 나눠 실행해 단독 시간이 아님. Docling GPU 실행은 flock 하에서 측정했으나 다른 세션 vLLM이 GPU를 공유 중이었다.

### 재측정 (동일 7개 파일 13쪽, 두 백엔드 순차 실행, load average 2~6, 다른 세션 작업 없음 확인, 전부 CPU, 기본 스레드)
| 종류 | Docling CPU median / p95 / mean | PP-StructureV3 CPU median / p95 / mean |
|---|---|---|
| digital (d06,d07; 7쪽) | 1.07 / 2.87 / 1.25 | 18.95 / 43.30 / 21.94 |
| scan (s01,s05; 2쪽) | 3.22 / 3.48 / 3.22 | 29.07 / 36.38 / 29.07 |
| table (t01,t02,t07; 3쪽) | 1.55 / 1.77 / 1.60 | 10.22 / 16.20 / 12.08 |
피크 RSS: Docling 2.9GB, PP 13.3GB (재측정). 표본이 작아 p95는 참고용. 부하가 완전히 0은 아니었으므로(load 2~6) 절대값보다 배율(약 10~15배)이 의미 있다.

### 전체 실행 (오염됨, 참고용)
| 실행 | digital median/p95 | scan median/p95 | table median/p95 |
|---|---|---|---|
| Docling GPU (flock, 다른 세션 GPU 공유) | 0.47 / 1.79 | 3.15 / 16.39 | 0.16 / 0.19 |
| Docling CPU (일부 구간 오염 가능) | 4.41 / 22.46 | 7.12 / 30.09 | 2.12 / 2.49 |
| PP CPU (오염+샤드) | 41.8 / 79.9 | 43.7 / 157 | 15.3 / 34.1 |
(PP digital 쪽 d01~d08은 중단된 단일 프로세스 로그에서 가져와 쪽별 분포 없이 평균 sec/page로 대체했다.) Docling GPU는 CPU 대비 약 5~13배 빠르다(표 0.16 vs 1.5~2.1초).

### 피크 메모리
- Docling: RSS 5.4~5.6GB(전체 실행), torch GPU 할당 피크 1.9GB.
- PP-StructureV3 CPU: 샤드별 RSS 12.0~12.5GB, 스캔 위주 샤드는 **66GB**(누수 또는 대형 이미지 처리 추정, 원인 미확인). 통합 메모리 128GB 기기에서는 동시 처리 시 위험.

## 종류별 승자
| 종류 | 승자 | 근거 |
|---|---|---|
| digital | 무승부(Docling이 속도 우위) | text match 동일. Docling은 쪽 실패 0 vs PP 116쪽 실패, 속도 10배 이상 |
| scan (한글 OCR) | PP-StructureV3 | 0.816 vs 0.63. 다만 느림(재측정 29 vs 3.2초/쪽) |
| table | Docling(전체), PP(깨끗한 합성 표) | 평균 0.843 vs 0.635, 실제 표에서 Docling 우세. 합성 단순 표는 PP 1.0 |
| 속도/메모리 | Docling | 위 표 |

## 권고
- 기본값: **Docling**. 신뢰도 중간. 이유는 GB10에서 GPU 사용 가능, 10배 이상 빠르고 메모리 작음, 쪽 실패 없음, 실제 표 정확도 우위.
- PP-StructureV3가 이기는 조건: (1) 스캔 한글 OCR 품질이 최우선이고 처리량·지연 요구가 낮을 때(OCR 유사도 +0.19), (2) 합성처럼 선이 뚜렷한 단순 표 위주일 때. 단 PP는 aarch64+GB10에서 GPU 불가하므로 x86 GPU 서버(공식 CUDA wheel)에서 재평가 필요.
- 조합 가능성: Docling 구조 + PP OCR 조합은 이 벤치에서 측정하지 않음(후속 후보).
- 신뢰도를 깎는 요인: 표 10건·스캔 합성 10건의 작은 표본, PP 어댑터 버그, 오염된 타이밍, 합성 스캔 위주.

## 어댑터 버그 수정 후 (품질만 재실행, 시간 재측정 없음)
- 수정: `folio/backends/ppstructure.py`의 `t.get("cell_box_list") or []`, `rec_scores or []`(numpy 배열에 `or` 사용)를 None 검사로 교체한 dev 코드(`~/folio-m1-bench/folio-dev/`)로 PP-StructureV3 CPU(4샤드 x 4스레드) 재실행. 실패했던 d09/d10/d11/d13/d14, 표 10건, 합성 스캔 10건 대상. 병합 결과 `out/backends/ppstructure_cpu_fixed.json`(나머지 문서는 수정 전 결과 이월; 해당 문서는 쪽 실패 0이었음). 재측정 시간은 없음(실행 중 다른 부하 존재).
- 결과 비교(전/후):

| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| 쪽 단위 구조 실패 | 116쪽 (d09 62, d10 5, d11 46, d13 1, d14 2) | **0** |
| digital text match | 0.9997 | 0.9997 (d09 0.9955로 동일) |
| 스캔 OCR 유사도(합성 10) | 0.816 | 0.816 (10건 모두 동일 값, 스캔 경로는 버그와 무관) |
| 표 cell_acc 평균 | 0.635 | 0.635 (10건 모두 동일) |
| 합성 표 t01~t06 | 1.0 x6 | 1.0 x6 |
| 실제 표 t07~t10 | 0.12, 0.0, 0.11, 0.11 | 0.12, 0.0, 0.11, 0.11 |

- **실제 표 붕괴(t07~t10, 0.0~0.12)는 수정 후에도 사라지지 않는다.** 버그와 무관한 별도 문제이며 원인은 미분리(rows/cols는 대체로 맞고 spans 불일치, 셀 텍스트 매핑 의심: 가설일 뿐 확인하지 않음). 후속 조사 필요.
- 수정으로 달라진 것은 digital 쪽 실패 116쪽이 사라진 것뿐이다. 이 쪽들의 구조(영역/표) 품질 자체는 text match로 측정되지 않아 개선 여부를 수치로 확인하지 못했다.
- **권고 변화: 없음.** Docling 우위(표 평균 0.843 vs 0.635, 실제 표, 속도, 메모리)는 그대로이고, PP의 스캔 OCR 우위(0.816 vs 0.63)도 그대로다. 이 결과는 증거이며 기본값 변경을 제안하는 것이 아니다: dev의 현재 기본값은 이미 PP-StructureV3 GPU(사용자 결정 2026-10-20)이다.
- 이 결정과 관련한 제약: Paddle GPU는 aarch64 sm_121(GB10)에서 `Unsupported GPU architecture`로 동작하지 않는다(위 GPU 가능성 참고). 따라서 GB10에서는 PP GPU 기본값을 쓸 수 없고 CPU로 떨어지며(재측정 기준 Docling CPU 대비 약 10~15배 느림), x86 GPU 서버 등 Paddle GPU 지원 환경에서만 의미가 있다. 이 속도 근거는 CPU 측정이며 PP GPU 속도는 측정되지 않았다.

## 셀 순서 버그 수정 후 (2026-10-21, 최상위 에이전트)

- 원인 확인: 실제 표에서 PP-StructureV3는 `cell_box_list`를 오른쪽 아래 셀부터 돌려준다(t08: 첫 상자 y=558 마지막 행, x 오른쪽→왼쪽). 어댑터는 이 목록을 `pred_html`의 `<td>` 순서(왼쪽 위부터)와 그대로 `zip`해서 표가 행·열 모두 뒤집혔다. 합성 표(t01)는 상자가 왼쪽 위부터 와서 문제가 없었다.
- 수정: `_reading_order`로 상자를 위→아래(행은 최소 셀 높이의 절반 이내 윗변끼리 묶음), 왼→오른쪽으로 정렬한 뒤 짝짓는다. 회귀 테스트 `test_ppstructure_cell_reading_order`.
- 재측정(DGX Spark CPU, `inspect_table.py`, 10개 표만, 시간 미측정):

| 표 | 수정 전 | 수정 후 | Docling |
|---|---|---|---|
| t01~t06 (합성) | 1.0 x6 | 1.0 x6 (spans 6/6) | 0.86~1.0 |
| t07 | 0.12 | 0.42 | 0.92 |
| t08 | 0.0 | 0.38 | 0.38 |
| t09 | 0.11 | 0.28 | 0.78 |
| t10 | 0.11 | 0.28 | 0.61 |
| 10개 평균 | 0.635 | 0.735 | 0.843 |

- 실제 표에서 남은 차이: spans 0/4, 셀 텍스트 중간 공백("헤어 메이 크업"), t08은 PP가 5행(정답 6행, 마지막 빈 병합 행)으로 셈. `cell_box_list` 20개와 `<td>` 24개처럼 개수가 다르면 짝짓기가 여전히 어긋난다. 지표가 셀 텍스트 정확 일치라 공백 차이도 오답으로 센다.
- 권고: Docling 우위는 유지되지만 차이가 줄었다(표 평균 0.843 vs 0.735, 실제 표 평균 0.67 vs 0.34).

## 한계
- PP GPU 미측정(미지원). GPU vs CPU 시간 직접 비교 금지. CPU끼리 비교는 재측정 13쪽만 유효.
- digital text match는 변별력 없음(텍스트 레이어 사용). 구조(헤딩/영역) 정확도는 측정하지 않음.
- 표는 합성/재렌더 PDF(텍스트 레이어 존재)이므로 표 인식은 구조 중심이며, 실제 스캔 표 OCR은 별도.
- 스캔 정답은 합성 스캔 10건만. r01~r04는 정성만.
- PP digital d01~d08은 중단된 실행에서 이어받음(품질 값만 신뢰, 시간 아님). PP 4개 샤드는 스레드 4개씩.
- 모든 결과는 공개 문서 기반 잠정치. 고객사 샘플로 재검증.

## 다운로드 / 설치 (DGX Spark)
- 환경(가상환경은 DGX Spark `~/folio-m1-bench/`): venv-docling 5.9GB (torch 2.14.1+cu130, docling), venv-paddle 1.6GB (paddlepaddle-gpu 3.3.1 cu129 wheel, paddleocr[doc-parser]), venv-paddle130 4.0GB (nightly 3.5.0.dev20260915 cu130, GPU 검증용, 삭제 가능).
- Docling 모델(HF 캐시): docling-project/docling-layout-heron, docling-project/docling-models(TableFormer), RapidOCR korean/latin ONNX (패키지 내장 또는 첫 실행 다운로드). HF blobs에는 다른 세션 모델도 섞여 있어 정확한 크기 분리 불가(레이아웃+TableFormer 합계 수백 MB 수준, 미측정).
- PaddleOCR(`~/.paddlex/official_models`): PP-DocLayout_plus-L 125M, RT-DETR-L_wired_table_cell_det 124M, RT-DETR-L_wireless_table_cell_det 124M, SLANeXt_wired 349M, SLANet_plus 7.8M, PP-OCRv5_server_det 85M, korean_PP-OCRv5_mobile_rec 14M, PP-LCNet_x1_0_doc_ori 6.7M, PP-LCNet_x1_0_table_cls 6.7M, PP-LCNet_x1_0_textline_ori 6.7M. 합계 약 850MB. 40GB 초과 단일 모델 없음.
- 원본 출력(DGX Spark): `~/folio-m1-bench/out/backends/` (`docling_cuda.json`, `docling_cpu.json`, `ppstructure_cpu.json`(병합), `ppstructure_cpu_shard*.json`, `retime_*.json`, 로그). 로컬 재현 파일: `run_manifest.py`, `run_all.sh`, `shard.py`, `merge.py`, `summarize.py`.
- 재현: `venv-uv`로 `uv venv`; docling venv는 `uv pip install --index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match torch torchvision` 후 `docling jsonschema -e folio-src`; paddle venv는 `paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/` 후 `paddleocr[doc-parser]`.
