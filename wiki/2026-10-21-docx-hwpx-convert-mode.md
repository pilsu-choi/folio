---
type: concept
title: DOCX·HWPX·HWP convert 모드 구현 (FR-20)
description: DOCX·HWPX·HWP를 PDF로 변환해 PDF 파싱 경로로 처리하는 convert 모드와 folio-convert 변환 서버의 구현·검증 기록
tags: [parse, convert, docx, hwpx, hwp, libreoffice, rhwp, deploy]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ../docs/INTERFACES.md
  - ./2026-10-21-docx-converter-benchmark.md
  - ./2026-10-20-hwpx-converter-benchmark.md
---

# DOCX·HWPX·HWP convert 모드 구현 (FR-20)

## 결정

- 변환은 별도 이미지 `folio-convert`(`folio convert-server`, :9100)가 맡고, api·worker는 `FOLIO_CONVERT_URL`로 호출한다. 구조 분석 서버·VLM과 같은 사이드카 방식이다. LibreOffice와 글꼴(수백 MB)이 메인 이미지에 들어가지 않고, GPL인 H2Orestart를 별도 프로세스·이미지에만 둔다는 [HWPX 벤치마크](2026-10-20-hwpx-converter-benchmark.md)의 조건을 지킨다.
- URL이 없으면 로컬 바이너리(`soffice`, `rhwp`)를 쓴다. 변환기는 `converter` 옵션으로 `module:function`이나 callable로 바꿀 수 있다(PRD 요구).
- 변환 순서: DOCX·PPTX는 LibreOffice, HWPX·HWP는 rhwp → 실패 시 LibreOffice + H2Orestart(설치된 경우만). 근거는 [DOCX 벤치마크](2026-10-21-docx-converter-benchmark.md)와 HWPX 벤치마크.
- 바이너리 HWP도 같은 경로에 넣었다. 이미지 검증에서 rhwp가 `.hwp`를 변환하는 것을 확인했기 때문이다. 옵션은 `hwpx_mode`를 같이 쓴다.
- `native` 모드(FR-21)는 범위 밖이다. `docx_mode`·`hwpx_mode="native"`는 `NotImplementedError`를 낸다.
- 변환 PDF는 저장하지 않는다. `meta.convert.pdf_hash`는 그 파싱에서 만든 PDF를 가리킬 뿐이다. 같은 DOCX를 LibreOffice로 두 번 변환하면 바이트가 달라 해시도 다르다(타임스탬프 포함, 크기는 같음, 실행 확인). rhwp는 같은 HWPX에 같은 바이트를 냈다. 해시로 PDF를 다시 찾거나 재현할 수는 없다. 인용 하이라이트에 변환 PDF를 보관할지는 PRD 열린 질문으로 남겼다. → 후속 결정: 보관한다(아래 "변환 PDF 보관").

## 구현

- `folio/parse/convert.py`: 공용 변환기. PPTX 전용이던 `convert_to_pdf`를 대체했다.
  - LibreOffice는 호출마다 별도 프로필(`-env:UserInstallation`)을 쓴다. 동시 실행이 같은 프로필을 공유하면 실패하기 때문이다.
  - 프로세스 그룹으로 실행해 시간 초과 시 자식(`soffice.bin`)까지 종료한다.
  - 종료 코드가 0이어도 PDF가 없으면 실패로 본다.
  - HWPX·HWP를 LibreOffice로 넘기는 것은 H2Orestart가 설치됐을 때만이다.
- `folio/parse/convert_server.py`: `POST /v1/convert`(multipart `file` + `mime`). 동시 변환 수는 `FOLIO_CONVERT_WORKERS`(기본 2)로 제한한다. 지원하지 않는 형식은 415, 변환 실패는 422를 돌려준다.
- `folio/parse/core.py`:
  - `docx_mode`·`hwpx_mode`(기본 `convert`), `converter`, `convert_timeout` 옵션을 추가했다.
  - 확장자가 없어도 zip 안의 `Contents/header.xml`로 HWPX를 판별한다.
  - `mime_type`·`content_hash`·`doc_id`는 원본 기준이고, 쪽·bbox는 변환 PDF 기준이다.
  - 수정: `pptx_mode="convert"`가 다른 형식에도 `finalize`를 걸던 조건을 고쳤다.
- `deploy/docker/Dockerfile.convert`: LibreOffice(Writer·Impress), rhwp v0.8.7(SHA256 확인), H2Orestart 0.8.0(`H2ORESTART=0`이면 제외), 공개 글꼴. `convert-fonts.conf`는 맑은 고딕·굴림·바탕·HY 계열·Calibri·Cambria 등을 나눔·Noto·Carlito·Caladea·Liberation으로 대체한다. 고객사 글꼴은 `/usr/share/fonts/custom`에 읽기 전용으로 마운트한다.
- Compose: `convert` 서비스(모든 프로필, 읽기 전용 루트, `FOLIO_CONVERT_CPUS=2`·`FOLIO_CONVERT_MEMORY=3g`), offline override, `.env.example`.
- Helm: `convert` Deployment·Service, `convert.*` values(workers, timeout, 글꼴 PVC), `external.convert.url`.
- 번들: `make_bundle.sh` 이미지 목록에 `folio-convert`를 추가했다.
- 문서: README(형식 표·파싱·배포 다이어그램), `docs/INTERFACES.md`, `deploy/README.md`, Helm README, PRD 구현 현황·열린 질문.

## 검증 (실행 결과)

- 단위 테스트(어절 분리 수정 전): 144개 통과, 1개 생략. 생략된 것은 로컬에 LibreOffice가 없어 건너뛴 실제 변환 시험이다. 새 `tests/test_convert.py`는 다음을 검증한다.
  - 변환 → 쪽·bbox, 원본 기준 식별자(DOCX·HWPX·HWP)
  - 확장자 없는 HWPX 판별
  - `native`·잘못된 모드
  - PDF가 아닌 응답
  - 로컬 체인 대체와 오류 기록
  - 시간 초과 시 프로세스 그룹 종료
  - 서버 왕복, 415·422 응답
- 이미지: `folio-convert`를 데스크탑(x86_64)에서 빌드했다. 1.64GB, LibreOffice 25.2.3.2(python:3.12-slim = Debian trixie), rhwp v0.8.7, H2Orestart 등록을 확인했다. `fc-match`로 맑은 고딕→NanumGothic, Batang→NanumMyeongjo, Calibri→Carlito 등을 확인했다.
- 인수 시험: 변환 서버 컨테이너(읽기 전용, CPU 4·메모리 4GB)에 대해 `folio.parse.default(..., backend="heuristic", vlm=False)`로 파싱했다.

| 형식 | 문서 | 쪽 | 블록 | bbox 없는 텍스트 블록 | 쪽 오류 | 변환 초/쪽 | 전체 초/쪽 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DOCX ([벤치마크 샘플](../bench/docx-converters/samples.txt)) | 13 | 300 | 4592 | 0 | 1 | 0.073 | 0.145 |
| HWPX ([rhwp 샘플](../bench/hwpx-converters/samples.txt)) | 11 | 217 | 2791 | 0 | 2 | 0.261 | 0.332 |
| HWP (rhwp 저장소 `samples/2010-01-06.hwp`) | 1 | 6 | 96 | 0 | 0 | 0.355 | 0.433 |

- 쪽 오류 3건은 글자 레이어가 약한 쪽(이미지 쪽)이 OCR로 넘어갔는데, 로컬 개발 환경에 `rapidocr_onnxruntime`이 없어서 생겼다. 배포 환경은 구조 분석 서버나 VLM이 처리한다. 변환 문제는 아니다.
- HWPX 쪽수는 11개 모두 한컴 정답 PDF와 같았다(HWPX 벤치마크와 같은 결과).
- 이미지의 LibreOffice 25.2와 글꼴 별칭으로 DOCX를 다시 평가했다(Word PDF 정답, `evaluate.py --out out-image`). 쪽수 일치 6/13, 텍스트 재현율 0.985, 어절 재현율 0.927, 위치 일치 0.110이다. 벤치마크의 LibreOffice 24.2·별칭 없는 공개 글꼴(5/13, 0.979, 0.869, 0.053)보다 모두 좋고, Windows 글꼴 실행(6/13, 0.983, 0.926, 0.126)에 가깝다.
- Compose: `docker compose config`(기본·offline) 통과. `convert` 서비스만 띄워 healthy, 읽기 전용 루트, CPU 2, 메모리 3GB를 확인했다.
- Compose E2E(`deploy/compose/e2e.sh`, profile `infra,dev`): 새 `convert_scenario`가 API로 `tests/fixtures/contract.docx`와 `E2E_HWPX`(form002.hwpx)를 올렸다. worker가 `convert` 서비스를 거쳐 처리해 두 작업 모두 `succeeded`였다. 저장된 `document.json`에 `meta.convert`(LibreOffice 25.2.3.2 / rhwp v0.8.7)가 있었고, figure를 뺀 모든 블록에 bbox가 있었다(DOCX 1쪽 3블록, HWPX 10쪽 99블록). 기존 PDF 시나리오도 통과했다.
- 변환 실패: worker는 모든 예외를 job `failed` + 메시지로 기록하고 재시도하지 않는다(`service/worker.py`). 서버에 연결할 수 없으면 `ConvertError("convert server ...: ConnectError ...")`가 나는 것을 단위 테스트로 확인했다. 실제 스택에서 변환 서버를 멈춘 시험은 하지 않았다.
- 연결 누락 수정: Airflow 예시 DAG의 DockerOperator 전달 env에 `FOLIO_CONVERT_URL`·`FOLIO_CONVERT_TIMEOUT`을 추가했다. KubernetesPodOperator는 Helm ConfigMap을 그대로 쓰므로 이미 들어 있다. `deploy/kind/e2e.sh` 이미지 목록과 태그에 `folio-convert`를 추가했다(kind E2E는 실행하지 않음).
- Helm: `helm lint`, `helm template`(values-dev)으로 기본·`convert.enabled=false`+`external.convert.url`·글꼴 PVC 렌더링을 확인했다.

- PPTX `convert` 모드: `tests/fixtures/slides.pptx`를 변환 서버 컨테이너(`FOLIO_CONVERT_URL`)로 보내 `pptx_mode="convert"`, `backend="heuristic"`으로 파싱했다. 4쪽 모두 720×540pt, 슬라이드 제목이 모두 나왔고 모든 블록에 bbox가 있었다. 휴리스틱 백엔드는 표를 조각내고 막대 차트를 빈 표로 잡았다. 이는 변환이 아니라 구조 분석 백엔드의 한계다. 슬라이드 원본 구조를 쓰는 `native`가 기본값으로 맞다. 이미지 안 실제 LibreOffice로 같은 변환을 하는 테스트(`test_real_libreoffice_pptx_convert_mode`)를 추가했다.

## 어절 분리 수정 (텍스트 레이어 추출기)

- 발견: 이미지 안에서 실제 LibreOffice DOCX 테스트가 실패했다. python-docx로 만든 문서(동아시아 글꼴 미지정, 테마 `ea typeface=""`)는 한글이 은바탕·은돋움 대체 글꼴로 그려진다. 이때 LibreOffice는 한글 어절을 공백 없이 한 글리프 묶음으로 그리고, 공백은 DejaVuSerif로 따로 그린다. PyMuPDF는 공백을 다른 줄로 묶어 `본계약은부품공급조건을정한다.`처럼 어절이 붙었다. `tests/fixtures/contract.docx`의 Compose E2E 결과도 같은 영향을 받았다.
- 같은 방식으로 rhwp·한컴·OnlyOffice PDF를 확인했다. 한컴 정답 PDF와 rhwp PDF도 한국어 어절 사이에 공백 글리프가 없는 곳이 많았다(`밑줄친㉠∼㉤과같은현상의…`). 기존 추출기는 공백 문자로만 단어를 나눴다.
- 수정: `folio.parse.textlayer.extract_atoms`가 글자 사이 간격으로도 어절을 나눈다. 기준은 그 줄의 보통 글자 간격(아래쪽 중앙값)보다 0.24em 넘게 벌어진 경우다. 간격은 줄의 쓰기 방향(보이는 쪽 기준, 쪽 회전·세로 글자 포함)으로 잰다. 축에 맞지 않는 기울어진 줄은 예전처럼 공백 문자로만 나눈다. 그래서 균등 분배된 글자(`경 상 북 도`)는 한 어절로 남는다. 압축 글꼴은 글자 상자가 겹치므로 0.5em까지 뒤로 겹쳐도 같은 어절로 본다. PyMuPDF가 어절 중간에서 줄을 나눈 경우, 앞 어절 끝에 바로 붙는 다음 줄은 그 어절을 이어 간다. 글자 단위(`chars`) 출력은 그대로라 PyMuPDF 원문과 같다.
- 결과(어절 재현율, 같은 추출기로 정답·후보를 읽음, 실행 결과):

| 후보 / 정답 | 기존 추출기 | 수정 추출기 |
| --- | --- | --- |
| LibreOffice 25.2 이미지 / Word PDF (DOCX 13) | 0.927 | 0.933 |
| OnlyOffice 9.4 / Word PDF (DOCX 13) | 0.380 | 0.896 |
| rhwp v0.8.7 이미지 / 한컴 PDF (HWPX 11) | 0.405 | 0.969 |

- 정답 PDF 자체의 단어 변화: Word PDF는 0.3%, 한컴 PDF는 8%가 바뀌었다. 한컴 PDF의 변화는 대부분 붙어 있던 어절이 나뉜 것이다. 남은 차이는 `□업종별` → `□` `업종별`, `개시*하였다.` → `개시` `*` `하였다.`, `김현철(053-…)` → 이름과 번호 분리 같은 경우다.
- 단위 테스트(`test_words_split_on_gaps_without_space_glyphs`, 쪽 회전 0·90·270)와 전체 테스트 147개가 통과했다. 쪽 회전 0~270과 글자 회전 0~270의 16개 조합에서 `합계 금액`이 두 어절로 나오는 것도 확인했다. 이미지 안에서 `tests/test_convert.py`·`tests/test_parse.py` 29개(실제 LibreOffice DOCX·PPTX 포함)가 통과했다.
- 이 수정은 PDF 입력 전체에 적용된다. 블록 텍스트는 어절을 공백으로 이어 붙이므로, PyMuPDF 원문에 없던 공백이 생길 수 있다(FR-01 수용 기준은 공백 정규화 후 비교로 명시). M1 PDF 벤치마크는 다시 돌리지 않았다.

## 변환 PDF 보관

- 결정(사용자, 2026-10-21): 인용 하이라이트를 위해 변환 PDF를 보관한다. LibreOffice는 다시 변환하면 바이트가 달라져, 저장된 bbox가 가리키는 PDF를 나중에 다시 만들 수 없기 때문이다.
- 크기(공개 샘플, 실행 결과): 변환 PDF 합계는 원본 대비 DOCX 1.11배(13개, 파일별 중앙값 3.6배, 최대 12.2배), HWPX 2.04배(11개, 중앙값 1.6배, 최대 13.3배)다. 글 위주의 작은 문서는 PDF가 몇 배 커지고, 이미지가 많은 문서는 비슷하거나 작다. 한글 글꼴 내장이 원인이라는 것은 추정이다(미확인). PDF 입력은 추가 용량이 없다.
- 구현: `folio.parse.default`가 변환한 PDF 바이트를 Document의 비직렬화 속성(`_converted_pdf`)으로 넘긴다. 파이프라인은 parse 단계에서 이를 `{index}/{doc_id}/converted.pdf`로 저장하고, `document.json`의 `meta.convert.pdf_key`와 `manifest.json`의 `converted_pdf`에 기록한다. 다시 parse하면 덮어쓰고, 변환 없이 parse된 경우(예: PPTX를 `native`로 다시 처리)에는 지운다. chunk·map부터의 재처리는 PDF를 건드리지 않는다. 문서 삭제는 `{index}/{doc_id}` 전체를 지우므로 함께 지워진다.
- 검증: 단위 테스트(`test_converted_pdf_is_stored_next_to_source`)로 저장, `pdf_hash` 일치, manifest 기록, chunk 재처리 후 유지를 확인했다. 전체 단위 테스트 158개 통과. 변환 서버 컨테이너(`folio-convert:dev`)를 띄우고 `run_pipeline(..., stop_after="parse")`로 `contract.docx`(LibreOffice, 47KB)와 `form002.hwpx`(rhwp, 929KB)를 처리했다. 두 문서 모두 `converted.pdf`가 저장됐고 `pdf_hash`와 일치했다. Compose E2E와 S3 저장소로는 확인하지 않았다.

## 하이라이트용 PDF API

- `GET /v1/documents/{doc_id}/pdf?pipeline=`: hit의 `page`·`bbox`가 가리키는 PDF를 준다. 변환 문서는 `converted.pdf`, PDF 입력은 `source.pdf`다. 이미지·PPTX `native`·XLSX처럼 PDF가 없으면 404다. `ETag`는 내용 해시다. 라이브러리 함수는 `folio.pipeline.document_pdf(cfg, doc_id)`다.
- 권한: folio API에는 인증·ACL이 없다(검색 API와 같다). 어떤 사용자가 어떤 `doc_id`를 열 수 있는지는 App이 판단한다. 예를 들어 그 사용자의 필터로 검색된 hit의 `doc_id`만 허용한다.
- 수정: `DELETE /v1/documents/{doc_id}`는 `doc_id`를 검사하지 않고 저장소 경로에 붙였다. `%2E%2E`(`..`)를 보내면 인덱스의 상위 디렉터리를 지울 수 있었다. 두 엔드포인트 모두 `doc_id`가 `[A-Za-z0-9_-]{1,128}`가 아니면 400을 준다. 없는 파이프라인 이름은 이제 500이 아니라 400이다.
- 검증: 서비스 테스트(`test_document_pdf_for_highlights`)로 PDF 원본 응답, `converted.pdf` 우선, 404, 잘못된 `doc_id` 400, 인코딩된 `..` 삭제 거부를 확인했다. 전체 단위 테스트 171개가 통과했다. 실제 API 서버·S3로는 확인하지 않았다.

## 미확인·남은 일

- arm64 이미지 빌드, `H2ORESTART=0` 빌드는 하지 않았다.
- kind·k3s 위 Helm 배포, 번들 빌드·설치는 하지 않았다. 번들 시험은 사용자 지시로 보류 중이다.
- 동시 부하(워커 여러 개가 변환 서버를 동시에 호출)와 장시간 안정성은 측정하지 않았다.
- 이미지 크기 중 상당 부분은 `service` extra의 의존성(celery, sqlalchemy, onnxruntime 등)이다. 변환 서버에는 fastapi·uvicorn만 필요하므로 extra를 나누면 줄일 수 있다.
- rhwp는 fontconfig 별칭을 쓰지 않고 자체 대체(Noto CJK)를 한다. `--fallback-sans`·`--fallback-serif`로 바꿀 수 있지만 설정으로 노출하지 않았다.
- 채팅 UI(FR-32)에 하이라이트를 붙이는 작업은 하지 않았다.
- 원격 저장소가 없어 로컬 `dev`에서 `feat/docx-hwpx-convert` 브랜치·worktree로 작업했다.
