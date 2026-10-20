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
- 변환 PDF는 저장하지 않는다. `meta.convert.pdf_hash`만 남긴다. 인용 하이라이트에 변환 PDF가 필요한지는 PRD 열린 질문으로 남겼다.

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

- 단위 테스트: 143개 통과, 1개 생략. 생략된 것은 로컬에 LibreOffice가 없어 건너뛴 실제 변환 시험이다. 새 `tests/test_convert.py`는 다음을 검증한다.
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
- Helm: `helm lint`, `helm template`(values-dev)으로 기본·`convert.enabled=false`+`external.convert.url`·글꼴 PVC 렌더링을 확인했다.

## 미확인·남은 일

- arm64 이미지 빌드, `H2ORESTART=0` 빌드는 하지 않았다.
- 전체 Compose E2E, kind·k3s 위 Helm 배포, 번들 빌드·설치는 하지 않았다. 번들 시험은 사용자 지시로 보류 중이다.
- PPTX를 변환 서버로 보내는 경로는 시험하지 않았다. 이미지에 Impress는 들어 있다.
- 동시 부하(워커 여러 개가 변환 서버를 동시에 호출)와 장시간 안정성은 측정하지 않았다.
- 이미지 크기 중 상당 부분은 `service` extra의 의존성(celery, sqlalchemy, onnxruntime 등)이다. 변환 서버에는 fastapi·uvicorn만 필요하므로 extra를 나누면 줄일 수 있다.
- rhwp는 fontconfig 별칭을 쓰지 않고 자체 대체(Noto CJK)를 한다. `--fallback-sans`·`--fallback-serif`로 바꿀 수 있지만 설정으로 노출하지 않았다.
- 변환 PDF 보관 여부(PRD 열린 질문).
- 원격 저장소가 없어 로컬 `dev`에서 `feat/docx-hwpx-convert` 브랜치·worktree로 작업했다.
