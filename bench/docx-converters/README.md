# DOCX → PDF 변환기 벤치마크

Microsoft Word가 저장한 PDF(`ref/`)를 정답으로 두고 LibreOffice, OnlyOffice(DocumentBuilder)의 PDF와 kordoc(native) 출력을 비교한다. 결과와 해석은 [wiki 기록](../../wiki/2026-10-21-docx-converter-benchmark.md)에 있다.

LibreOffice·kordoc 이미지, 글꼴(`fonts/`), 평가 스크립트는 [HWPX 벤치마크](../hwpx-converters/)의 것을 그대로 쓴다.

```bash
bash ../hwpx-converters/fetch.sh   # 공개 글꼴 ../hwpx-converters/fonts/ (HWPX 샘플도 함께 받음)
bash fetch.sh                      # samples/ 다운로드, WSL이면 Word로 ref/ 생성
bash run.sh                        # 이미지 빌드 후 도구별 순차 변환, out/<tool>/ (CPUS·MEM으로 제한)
python ../hwpx-converters/evaluate.py . --ext docx --pdf-tools libreoffice,onlyoffice --native-tools kordoc
```

- 정답 PDF는 Windows의 Word로 만든다(`word_export.ps1`, COM 자동화, 매크로 비활성, 읽기 전용). Word가 열려 있으면 실행을 거부한다. Word에 로그인·활성화 대화상자가 떠 있으면 숨은 인스턴스가 멈추므로, 먼저 Word를 한 번 직접 열어 대화상자를 처리하고 닫는다.
- 글꼴 영향 확인: `FONTS=/mnt/c/Windows/Fonts OUT=out-winfonts TOOLS="libreoffice onlyoffice" bash run.sh` 후 `evaluate.py ... --native-tools "" --out out-winfonts`. Windows 글꼴은 그 PC에서 읽기 전용으로만 마운트하고 다른 서버로 복사하지 않는다.
- Linux 서버에서 변환할 때는 `samples/`와 `ref/`를 함께 옮긴다.
- 고객사 샘플로 비교할 때는 `samples/<name>.docx`를 넣고 `word_export.ps1`로 `ref/<name>.pdf`를 만든다.
