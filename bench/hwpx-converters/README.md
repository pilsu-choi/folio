# HWPX → PDF 변환기 벤치마크

한컴 오피스가 저장한 PDF(`ref/`)를 정답으로 두고 rhwp, LibreOffice + H2Orestart, hwpforge의 PDF와 kordoc(native) 출력을 비교한다. 결과와 해석은 [wiki 기록](../../wiki/2026-10-20-hwpx-converter-benchmark.md)에 있다.

```bash
bash fetch.sh          # samples/, ref/, fonts/ 생성 (Git에 넣지 않음)
bash run.sh            # 이미지 빌드 후 도구별 순차 변환, out/<tool>/
python evaluate.py .   # pymupdf, rapidfuzz 필요. eval.json + 표 출력
```

고객사 샘플로 비교할 때는 `samples/<name>.hwpx`와 한컴에서 저장한 `ref/<name>.pdf`를 같은 이름으로 넣는다.

국가법령정보센터 법령 본문(서버 생성 HWPX + iText PDF)은 `python fetch_law.py privacy=283839 labor_standards=265959 ...`로 받는다. 이 PDF는 쪽 나눔 정답이 아니므로 텍스트 재현율만 본다.
