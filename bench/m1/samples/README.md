# M1 벤치마크 공통 샘플 세트 (잠정, 공개 문서 기반)

고객사 샘플의 대리로 쓰는 공개 한글 문서 세트다. 모든 결과는 "잠정, 고객사 샘플로 재검증".
데이터는 Spark `~/folio-m1-bench/samples/`에만 있다(Git 제외). 이 디렉터리에는 생성 스크립트, `manifest.json` 사본, 이 문서만 둔다.

## 구성 (50개, 345쪽, 46MB)

| kind | 개수 | 내용 | 정답 |
|---|---|---|---|
| digital | 16 (311쪽) | FSC 보도자료 8, KOSTAT 보도자료 3, FTC 표준약관 5 (hwpx를 PDF로 재렌더) | `truth/<id>.page<N>.txt` (페이지당 텍스트 레이어, 문서당 최대 3쪽) |
| scan | 14 | 합성 스캔 10 (`s01..s10`) + 실제 스캔 4 (`r01..r04`) | 합성: `.page1.txt` + `.truth.json` `{"lines":[...]}` / 실제: 없음 |
| table | 10 | 합성 HTML 표 6 (`t01..t06`) + 실제 표 4 (`t07..t10`, FTC 표준약관 hwpx 셀 정보) | `truth/<id>.table.json` |
| figure | 10 | 차트/도식 크롭 (FSC 실태조사, KOSTAT 고용동향, FSC K-GX 전략) | `truth/<id>.figure.json` `{"description","facts":[...]}` |

- 장문(30쪽 이상) 4개: `d01_fsc_kgx`(61쪽, 장/절 번호 헤딩), `d09_kostat_employment`(81쪽), `d10_kostat_elderly`(42쪽), `d11_kostat_tables`(46쪽, 숫자표 부록).
- 헤딩 계층이 뚜렷한 청킹용 문서: d01, d09, d10, d12/d13/d15/d16 (제N조/항 구조 약관), d14 (조 + 양식 표).
- 표 정답 형식은 bench 하네스(`folio-src/scripts/bench_backends.py`)와 같다: `{"page","rows","cols","cells":[[str]],"spans":[{"row","col","rowspan","colspan"}]}`. 병합 셀 텍스트는 좌상단에만, 덮인 칸은 `""`. 스캔 정답은 `{"lines":[...]}`.

## 경로

```
~/folio-m1-bench/samples/
  manifest.json            # [{id,file,kind,pages,source_url,license,truth[],notes}]
  pdf/   d01..d16 *.pdf
  scan/  s01..s10 *.jpg (합성), r01..r04 *.jpg (실제)
  table/ t01..t10 *.pdf + *.png (200dpi)
  figure/ f01..f10 *.png (150dpi)
  truth/ <id>.page<N>.txt | <id>.truth.json | <id>.table.json | <id>.figure.json
~/folio-m1-bench/folio-src/  # git archive 753561e
~/folio-m1-bench/.venv       # pymupdf pillow numpy weasyprint requests
~/folio-m1-bench/stage/      # 원본 다운로드 (fsc, kostat, ftc, hwpx, real)
```

## 출처와 라이선스 (잠정)

- FSC 보도자료: `https://www.fsc.go.kr/no010101/<postNo>` 첨부 PDF (정부 공공저작물). 문서별 공공누리(KOGL) 유형은 개별 확인하지 않았다. 출처 표시 후 내부 벤치마크 용도로만 사용하고, 재배포 전에 확인한다.
- KOSTAT(국가데이터처) 보도자료: `https://kostat.go.kr/board.es?mid=a10301010000&bid=210` 첨부 PDF. 위와 같은 주의.
- FTC 표준약관: `https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=201&key=201` 의 hwpx 첨부를 `hwpx2pdf.py`로 PDF 재렌더. 텍스트 레이어는 정확하나 레이아웃은 원본과 다르다.
- 실제 스캔: Wikimedia Commons `File:Gwanbo_jeilho.pdf`(관보 1호, 1948), Wikisource `CNTS-00115191829 自由新聞 1948-07-13.pdf`. 각 파일 페이지에 Public domain으로 표기됨.
- 합성 표, 합성 스캔, 그림 설명: 자체 제작.

## 재생성 (Spark, `cd ~/folio-m1-bench`, `.venv/bin/python`)

1. `fetch_fsc.py stage/fsc 5`, `fetch_gov.py` (KOSTAT/FTC 목록 URL과 링크 정규식은 파일 docstring 참고), FTC hwpx는 `hwpx2pdf.py`로 `stage/hwpx/`에 변환. 목록은 시간이 지나면 바뀌므로 샘플 세트는 이미 받은 파일을 기준으로 한다.
2. `make_digital.py` -> `make_scans.py` -> `make_real_scans.py` -> `make_tables.py` -> `make_figures.py` + `figure_truth.py` -> `make_manifest.py`. 합성 스캔과 표는 시드 고정으로 결정적이다.

## 한계 / 약점

- 법령 원문(장/절/조): law.go.kr은 PDF 직접 다운로드 경로를 찾지 못해 제외했다. 대신 조/항 구조의 FTC 표준약관(d12, d13, d15, d16)과 FSC 입법예고(d07, 신구조문대비표)를 썼다. 장/절이 있는 긴 법령은 없다.
- KDI, 국회입법조사처 보고서는 포함하지 못했다(KDI는 다운로드 경로 미확인, 입법조사처는 DRM 뷰어).
- 실제 스캔 4쪽은 1948년 국한문혼용 문서(관보 1호 2쪽, 자유신문 2쪽)로 정답이 없고 현대 문서보다 훨씬 어렵다. 정량 비교는 합성 스캔 10쪽으로 한다. 현대 한글 실제 스캔은 확보하지 못했다.
- 합성 스캔 정답은 원본 PDF 텍스트 레이어의 줄 단위이며, 읽기 순서/줄바꿈이 다단 문서에서 이미지와 다를 수 있다.
- 합성 표 6개는 가상 데이터. 실제 표 4개는 hwpx에서 셀 병합 정보를 직접 읽어 정답이 정확하나, 표만 단독 재렌더한 것이다.
- 그림 설명과 facts는 크롭을 보고 사람이 작성했다. 그림은 한 문서군(FSC/KOSTAT)에 치우쳐 있다.
- 디지털 PDF의 텍스트 정답은 PyMuPDF 텍스트 레이어이므로 읽기 순서 오류가 정답에 섞일 수 있다.
