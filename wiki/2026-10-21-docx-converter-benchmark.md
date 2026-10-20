---
type: concept
title: DOCX → PDF 변환기 벤치마크 (LibreOffice · OnlyOffice · kordoc)
description: Word 저장 PDF를 정답으로 DOCX 변환기 2종과 native 파서 1종을 비교한 실행 기록과 잠정 결정
tags: [docx, parse, convert, benchmark, libreoffice, onlyoffice, kordoc]
status: draft
generated: 2026-10-21
sources:
  - ../PRD.md
  - ../bench/docx-converters/README.md
  - ./2026-10-20-hwpx-converter-benchmark.md
  - https://github.com/ONLYOFFICE/DocumentBuilder
  - https://github.com/chrisryugj/kordoc
---

# DOCX → PDF 변환기 벤치마크

## 결정 (잠정)

- `convert` 모드의 DOCX → PDF 기본 변환기: **LibreOffice**(`soffice --convert-to pdf:writer_pdf_Export`). HWPX 대체 변환기와 같은 이미지라 이미지를 하나 더 늘리지 않는다.
- OnlyOffice(DocumentBuilder)는 기본값에서 제외한다. 실제 글꼴이 있으면 레이아웃은 LibreOffice와 비슷하거나 낫지만, 한국어 PDF 텍스트 레이어에 공백 문자를 넣지 않는다. folio가 쓰는 pymupdf로 읽으면 어절이 붙어 나온다(`본발명은하역부두에서…`). 라이선스도 AGPL-3.0이다.
- kordoc은 HWPX와 마찬가지로 `native` 모드 구현 시 비교 기준으로 쓴다.
- 고객사 샘플로 재확인하기 전까지 잠정 결정이다. [PRD](../PRD.md) `DOCX · HWPX` 절에 반영했다.

## 방법

- 입력: 공공데이터포털(data.go.kr)·공공기관·대학이 공개한 Word 작성 한국어 DOCX 11개, 영어 테스트 문서 2개(Apache POI, Open-XML-SDK, 커밋 고정). 합계 319쪽(Word 기준). 서식 표(병합 셀), 머리말·꼬리말, 다단·가로 구역, 목차 필드, 이미지가 많은 85쪽 보고서를 포함한다. 목록과 URL은 [samples.txt](../bench/docx-converters/samples.txt). 개인 연락처가 본문에 있는 문서, 메모가 달린 문서는 뺐다.
- 정답: 데스크탑 Windows의 Microsoft 365 Word 16.0.20430으로 COM 자동화 내보내기(`ExportAsFixedFormat`, 매크로 비활성, 읽기 전용). 정답 PDF는 맑은 고딕·굴림·바탕·돋움·Times New Roman·Arial 등 Windows 글꼴을 쓴다.
- 후보: LibreOffice 24.2(Ubuntu 24.04 apt, HWPX 벤치마크와 같은 Dockerfile. PDF producer 기준이며 패치 버전은 기록하지 않았다), OnlyOffice DocumentBuilder 9.4.0(`docbuilder` 스크립트로 PDF 저장), kordoc 4.22.15(native, Markdown).
- 실행 1(본 측정): DGX Spark(`pgx`, aarch64)에서 도구를 하나씩 순차 실행, 컨테이너당 CPU 4개·메모리 6GB 제한, 파일마다 2회 중 짧은 시간. 글꼴은 HWPX와 같은 공개 글꼴(Noto CJK, 나눔, 은)만 마운트했다.
- 실행 2(글꼴 통제): 데스크탑(x86_64)에서 Windows 글꼴 폴더를 읽기 전용으로 마운트해 LibreOffice·OnlyOffice만 다시 변환했다. Windows 글꼴은 다른 서버로 복사하지 않았다. 시간은 9p 마운트 영향이 있어 쓰지 않는다.
- 지표([evaluate.py](../bench/hwpx-converters/evaluate.py), HWPX와 공용): 쪽수 일치, 텍스트 재현율(공백 제거 LCS·순서 무관 2-gram), **어절 재현율**(이번에 추가, 공백 단위 토큰의 순서 무관 재현율), 쪽별 텍스트 유사도, 단어 위치 일치(6pt 이내 비율, 중앙값).
- 재현: `bench/docx-converters/`의 `fetch.sh` → `run.sh` → `evaluate.py --ext docx`. 상세는 [README](../bench/docx-converters/README.md).

## 결과 (실행 결과)

실행 1: 공개 글꼴만, DGX Spark

| 도구 | 성공 | 쪽수 일치 | LCS 재현율 | 2-gram 재현율 | 어절 재현율 | 쪽별 유사도 | 위치 일치 @6pt | 위치 오차 중앙값 | 쪽당 시간 | 이미지 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LibreOffice 24.2 | 13/13 | 5/13 | 0.979 | 0.988 | 0.869 | 0.571 | 0.053 | 11.2pt | 0.054초 | 773MB |
| OnlyOffice DocumentBuilder 9.4.0 | 13/13 | 4/13 | 0.961 | 0.971 | 0.380 | 0.513 | 0.043 | 8.9pt | 0.069초 | 364MB |
| kordoc 4.22.15 (native, PDF 없음) | 13/13 | - | 0.918 | 0.909 | 0.890 | - | - | - | 0.039초 | 878MB |

실행 2: Windows 글꼴 마운트, 데스크탑

| 도구 | 쪽수 일치 | LCS 재현율 | 2-gram 재현율 | 어절 재현율 | 쪽별 유사도 | 위치 일치 @6pt | 위치 오차 중앙값 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LibreOffice 24.2 | 6/13 | 0.983 | 0.991 | 0.926 | 0.626 | 0.126 | 8.9pt |
| OnlyOffice DocumentBuilder 9.4.0 | 6/13 | 0.984 | 0.989 | 0.399 | 0.664 | 0.158 | 7.2pt |

- 글꼴 영향: 같은 글꼴을 주면 두 변환기 모두 좋아지고, 영어 문서는 OnlyOffice가 Word와 더 가깝다(en_business_plan 위치 일치 0.91, LibreOffice 0.46). 한국어 문서의 위치 일치는 두 도구 모두 0.0~0.4로 낮다. Windows 글꼴로 쪽수까지 맞은 LibreOffice kogas_ip(21/21, 위치 일치 0.02)에서 같은 단어 쌍의 오차를 보면 가로는 중앙값 -0.3pt로 맞고, 세로는 중앙값 -28pt(사분위 -39~-13pt)로 글자가 위로 올라가 있다. gitc_forms도 세로 -33pt다. 줄 높이·문단 간격 차이가 쪽 아래로 누적되는 것으로 보이며, Word의 문서 격자(`docGrid`)·동아시아 줄 간격 처리 차이가 원인이라는 것은 가설이다(미확인).
- 위치 지표 한계: 같은 문자열의 단어끼리 짝짓기 때문에, 어절이 붙는 OnlyOffice의 한국어 문서 위치 일치는 레이아웃보다 공백 손실을 반영한다. 쪽수는 글꼴을 맞춰도 kolid_trends(다단·가로 구역, 14/20), gkl_csr, nrra 문서들에서 2~6쪽 적다.
- OnlyOffice 공백: PDF에 공백 글리프를 넣지 않고 글자 간격으로만 띄운다. pdftotext는 간격으로 공백을 복원하지만 pymupdf는 어절을 붙여 출력한다. 글꼴과 무관하게 어절 재현율이 0.38~0.40이다. folio의 텍스트 레이어 추출기(`folio.parse.textlayer.extract_atoms`, pymupdf `rawdict` 기반)로 kogas_ip 3쪽을 직접 읽어 확인했다. OnlyOffice는 `본발명의조류계인양장치는조류계와결합하고…` 같은 붙은 어절이 나오고, LibreOffice는 정상이다. 키워드 검색(BM25)과 청킹에 바로 영향을 준다.
- OnlyOffice 쪽수: 공개 글꼴에서는 kangwonland_outlook 107/85, yonsei_thesis 25/20처럼 쪽이 크게 늘었다. 텍스트 누락도 일부 있었다(kogas_beacon 재현율 0.87, yonsei_thesis 0.86). Windows 글꼴에서는 각각 85/85, 21/20으로 줄었다.
- LibreOffice: 공개 글꼴에서도 재현율 0.94 이상이고 실패는 없다. en_poi_tables(17/14)·knfc_brief(4/3)는 쪽이 늘고, 다단 문서는 쪽이 줄었다. 대체 글꼴로 은돋움·은바탕·DejaVu를 고른다.
- kordoc: 원문 기준이라 쪽마다 반복되는 표 머리행과 머리말·꼬리말이 없어 Word PDF 대비 재현율이 낮게 나온다(nrra_cap 0.72, kangwonland_outlook 0.83). nrra_cap은 본문의 `<element …>` 예시를 평가 스크립트가 HTML 태그로 지운 영향도 있다. 정밀도는 0.95다.
- Word 자동화: 내보낼 경로를 `Join-Path` 결과(PSObject) 그대로 넘기면 `ExportAsFixedFormat`이 대화상자 없이 멈췄다. `[string]`으로 바꿔 해결했다. Word를 처음 쓰는 계정은 로그인 등 대화상자를 한 번 처리해야 한다.

- 후속(2026-10-21): 텍스트 레이어 추출기가 글자 간격으로도 어절을 나누도록 바뀌었다. 같은 PDF를 다시 읽으면 어절 재현율은 OnlyOffice 0.38 → 0.89, LibreOffice(이미지 25.2) 0.927 → 0.933이다. 위 표는 기존 추출기 기준으로 그대로 둔다. OnlyOffice 제외 결정은 쪽수 증가와 AGPL 때문에 유지한다. 상세는 [convert 모드 작업 기록](2026-10-21-docx-hwpx-convert-mode.md).

## 한계와 미확인

- 샘플이 공공기관 공개 문서와 영어 테스트 문서다. 고객사 문서로 반복해야 한다.
- 정답이 Windows 글꼴로 렌더링되어, 공개 글꼴만 쓰는 컨테이너는 위치·쪽수에서 불리하다. 고객사가 쓰는 MS 글꼴은 라이선스상 이미지에 넣을 수 없는 경우가 많으므로 대체 글꼴 매핑을 설정으로 둔다(HWPX와 같음).
- 표 셀 구조 일치는 측정하지 않았다. 변환 PDF를 Parse 경로에 넣은 블록·표 비교는 FR-20 구현 후 한다.
- 동시 실행 안정성, LibreOffice 프로세스 재사용(unoserver 등)은 확인하지 않았다. 시간은 컨테이너 시작을 포함한 문서당 단일 프로세스 기준이다.
- Aspose.Words 등 상용 변환기와 Word 자체(Windows 의존)는 실행 환경 기준에 맞지 않아 시험하지 않았다. Gotenberg는 LibreOffice와 같은 엔진이라 따로 재지 않았다.
- `evaluate.py`를 확장(`--ext`, 도구 목록, `--out`, 어절 재현율)했다. HWPX 기본 동작은 그대로지만 HWPX 샘플로 다시 실행해 수치를 대조하지는 않았다. `eval.json`은 이제 출력 디렉터리(`out/`) 안에 쓴다.
- 샘플 대부분이 data.go.kr 데이터셋이며 데이터셋별 이용 조건(공공누리 유형 등)은 확인하지 않았다. 저장소에는 URL만 두고 파일은 넣지 않는다.
- 원격 저장소가 없어 로컬 `dev`에서 `bench/docx-converters` 브랜치·worktree로 작업했다.
