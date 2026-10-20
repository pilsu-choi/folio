---
type: concept
title: HWPX → PDF 변환기 벤치마크 (rhwp · LibreOffice+H2Orestart · hwpforge · kordoc)
description: 한컴 저장 PDF를 정답으로 HWPX 변환기 3종과 native 파서 1종을 비교한 실행 기록과 결정
tags: [hwpx, parse, convert, benchmark, rhwp, libreoffice, h2orestart, hwpforge, kordoc]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../bench/hwpx-converters/README.md
  - https://github.com/edwardkim/rhwp
  - https://extensions.libreoffice.org/en/extensions/show/27504
  - https://pypi.org/project/hwpforge/
  - https://github.com/chrisryugj/kordoc
---

# HWPX → PDF 변환기 벤치마크

## 결정 (잠정)

- `convert` 모드의 HWPX → PDF 기본 변환기: **rhwp CLI**. 대체 변환기: **LibreOffice + H2Orestart**.
- hwpforge는 제외한다. kordoc은 `native` 모드 구현 시 비교 기준으로 쓴다.
- 고객사 샘플로 재확인하기 전까지 잠정 결정이다. [PRD](../PRD.md) `DOCX · HWPX` 절에 반영했다.

## 방법

- 입력: rhwp 저장소(커밋 `1a76570`)의 한컴 저장 HWPX 11개(총 217쪽). 공문서·보도자료·서식·시험지·RFP·평가표를 포함한다. 정답은 같은 저장소의 한컴 오피스 2020/2022 PDF.
- 글꼴: 모든 도구에 같은 공개 글꼴(Noto CJK, 나눔, 은)만 마운트했다. 원본이 쓰는 한컴·HY·MS 글꼴은 없다.
- 실행: 도구별 Docker 이미지. 최종 측정은 DGX Spark(`pgx`, aarch64, 20코어)에서 도구를 하나씩 순차 실행하고, 파일마다 2회 중 짧은 시간을 썼다. 데스크탑(x86_64) 1차 실행의 품질 수치는 Spark 결과와 같았다. 데스크탑 시간은 다른 세션 부하(load 30) 때문에 버렸다.
- 지표([evaluate.py](../bench/hwpx-converters/evaluate.py)): 쪽수 일치, 텍스트 재현율(공백 제거 후 LCS·순서 무관 2-gram), 쪽별 텍스트 유사도, 단어 위치 일치(같은 쪽·같은 단어 중심점, 정답 쪽 크기로 정규화, 6pt 이내 비율과 중앙값).
- 재현: `bench/hwpx-converters/`의 `fetch.sh` → `run.sh` → `evaluate.py`.

## 결과 (실행 결과)

| 도구 | 성공 | 쪽수 일치 | LCS 재현율 | 2-gram 재현율 | 쪽별 유사도 | 위치 일치 @6pt | 위치 오차 중앙값 | 쪽당 시간 | 이미지 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rhwp 0.8.7 | 11/11 | 11/11 | 0.977 | 0.992 | 0.956 | 0.338 | 1.9pt | 0.197초 | 169MB |
| LibreOffice 24.2.7 + H2Orestart 0.8.0 (JRE 21) | 11/11 | 5/11 | 0.959 | 0.968 | 0.638 | 0.045 | 11.5pt | 0.086초 | 773MB |
| hwpforge 0.16.11 | 0/11 | - | - | - | - | - | - | - | 434MB |
| kordoc 4.22.15 (native, PDF 없음) | 11/11 | - | 0.950 | 0.954 | - | - | - | 0.034초 | 878MB |

- rhwp: 쪽 나눔이 모두 한컴과 같다. 화면 비교에서 표·서식 레이아웃이 한컴과 거의 같다. 위치 일치율이 0.34에 그치는 것은 대체 글꼴로 글자 폭이 달라 줄 안에서 1~2pt씩 밀리기 때문이다. `LAYOUT_OVERFLOW`, `LAYOUT_TABLE_OVERLAP` 경고만 있고 실패는 없다. 66쪽 문서에 11초가 걸려 가장 느렸다.
- LibreOffice + H2Orestart: 가장 빠르지만 쪽수가 6개 문서에서 달랐다(kwater 32/27, pii 73/66, press_fdi 12/9). form002에서는 서식 표의 체크박스 행 내용이 비어 텍스트 재현율이 0.89였다. H2Orestart는 GPL이므로 별도 프로세스·이미지로만 호출한다.
- hwpforge: 글꼴과 무관한 실패였다. 8개는 `PDF_RENDER_FAILED: non-default table position`, 3개는 `DECODE_FAILED`(header.xml 음수 값, 행 안 헤더 표시 혼재, `scaMatrix` 중복)였다.
- kordoc: 오프라인(`--network none`)으로 동작한다. 블록별 `pageNumber`(한컴 레이아웃 캐시 기반)와 병합 셀 표 구조를 주지만 bbox는 없다. 글상자·각주 일부를 빠뜨렸다(loan_press 0.90, giving_form 0.89).

## 한계와 미확인

- 샘플이 rhwp의 회귀 테스트 문서여서 rhwp에 유리할 수 있다. 첫 고객사 샘플로 반복해야 한다.
- 한컴 원본 글꼴 없이 측정했다. 고객사가 글꼴 라이선스를 가지면 위치 정확도는 달라진다.
- 표 셀 구조 일치는 직접 측정하지 않았다. 텍스트와 화면 비교로만 확인했다. 변환 PDF를 Parse 경로에 넣은 블록·표 정답 비교는 Parse 구현 후 한다.
- 처리 시간은 순차 단일 프로세스 기준이다. 동시 실행 안정성은 확인하지 않았다.
- 한컴 한글 SDK와 EWL 방식은 시험하지 않았다.
- 원격 저장소가 없어 로컬 `dev`에서 `docs/hwpx-converter-bench` 브랜치·worktree로 작업했다.
