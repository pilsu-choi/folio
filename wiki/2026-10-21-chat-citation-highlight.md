---
type: concept
title: 채팅 App 근거 원문 하이라이트
description: 채팅 App 근거 패널에서 인용된 쪽의 근거 영역(bbox)을 칠한 이미지와 원문 PDF를 보여 주는 기능의 결정·구현·검증 기록
tags: [app, chat-ui, citation, highlight, bbox, parse]
status: draft
generated: 2026-10-21
sources:
  - ../apps/chat/main.py
  - ../apps/chat/static/index.html
  - ../tests/test_chat_app.py
  - ../folio/parse/textlayer.py
  - ./2026-10-21-chat-app-sso.md
  - ./2026-10-21-docx-hwpx-convert-mode.md
---

# 채팅 App 근거 원문 하이라이트

## 결정

- 사용자 요청: 채팅 UI(FR-32)에서 인용 근거를 원문에서 하이라이트한다. 앞선 작업으로 변환 PDF 보관(`converted.pdf`)과 folio `GET /v1/documents/{doc_id}/pdf`가 생겼다([convert 모드 기록](2026-10-21-docx-hwpx-convert-mode.md)).
- 그리기는 App 서버에서 한다. App이 folio에서 PDF를 받아 PyMuPDF(folio 기본 의존성)로 인용된 쪽을 렌더링하고, `bbox`에 반투명 사각형을 칠해 PNG로 준다. pdf.js 같은 브라우저 라이브러리를 쓰지 않으므로 UI는 계속 외부 리소스가 없고 폐쇄망에서 동작한다(기존 테스트 `test_index_served`가 확인).
- 근거 패널이 좁으므로 쪽 전체 대신 `bbox` 주변 36pt까지 잘라서 보여 준다. 이미지를 누르면 PDF 전체가 그 쪽(`#page=N`)에서 열린다.
- 권한: folio API에는 ACL이 없으므로 App이 막는다. 사용자는 자기 대화에 저장된 답변의 `hits`에 있는 문서만 열 수 있다. 그 hit는 로그인 ACL 필터로 검색된 결과다. 권한이 나중에 회수되어도 이미 저장된 답변의 근거는 열린다(대화 기록과 같은 수준).

## 구현

- `apps/chat/main.py`
  - `GET /api/conversations/{id}/messages/{mid}/sources/{n}/page.png`: 답변 `mid`의 `n`번째 hit에서 `doc_id`·`page`·`bbox`를 읽는다. 2px/pt로 렌더링하고, 하이라이트와 잘라내기를 적용한다. `page`가 없으면 404다.
  - `GET .../sources/{n}/pdf`: folio의 PDF를 그대로 전달한다.
  - 대화 소유, 메시지가 답변인지, `n` 범위를 확인한다. folio의 400·404는 404로, 나머지 오류는 503으로 바꾼다.
  - `bbox`는 보이는 쪽 기준(회전 반영) 좌표다. 사각형은 `derotation_matrix`로 PDF 좌표로 바꿔 그리고, 잘라내기(`clip`)는 보이는 쪽 좌표 그대로 쓴다. 처음에는 `clip`도 변환했다가 회전된 쪽에서 잘못 잘리는 것을 확인하고 고쳤다.
- `apps/chat/static/index.html`: `page`가 있는 근거 카드에 "원문 보기"·"원문 닫기" 버튼을 둔다. 이미지는 누를 때만 불러온다. folio가 PDF를 두지 않는 형식(이미지, XLSX, Markdown, TXT; `source_uri` 확장자로 판단)은 버튼을 숨긴다. PPTX는 `native`(기본)면 PDF가 없고 `convert`면 있어서 버튼을 보이고, 실패하면 "원문 PDF가 없습니다…(PPTX는 convert 모드)"를 표시한다.
- `folio/parse/textlayer.py`(별도 커밋): E2E에서 `제1조 목적:`이 청크 본문에서 `제1조`와 `목적:` 두 줄로 나뉘었다. LibreOffice PDF에서 PyMuPDF가 한 줄을 여러 줄로 나누고, 블록 텍스트는 줄 id마다 줄을 바꾸기 때문이다. 이 문제는 어절 분리 수정 이전에도 있었다.
  - 수정: 앞 줄의 마지막 글자와 같은 기준선에서 공백 하나(약 0.4em) 이내로 이어지는 줄은 같은 줄 id를 쓴다.
  - 처음에는 1em 이내로 잡았다. 그러자 한컴·rhwp PDF 22개(16,626줄)에서 850쌍이 합쳐졌고, 그중 830쌍이 양쪽 정렬로 넓어진 띄어쓰기나 표의 이웃 칸(`1분기`·`2분기`)이었다. 기준을 줄이자 5쌍만 합쳐졌고, 5쌍 모두 글꼴이 바뀌는 지점(`‧`, `｢`)에서 이어지는 진짜 연속이었다.
  - 함께 수정: 글자 간격이 하나뿐인 줄(두 글자)은 그 간격을 보통 글자 간격으로 보지 않는다. 이전에는 `품 가`처럼 공백 글리프 없이 띄운 두 글자가 한 어절로 붙었다. rhwp 어절 재현율은 0.969 → 0.972다.
  - 단위 테스트는 같은 줄 id 유지와 두 글자 줄 분리를 확인한다.

## 검증 (실행 결과)

- 단위 테스트 `test_source_page_highlight_and_pdf`(쪽 회전 0·90)는 다음을 확인한다.
  - PNG 크기가 bbox + 36pt와 같다.
  - bbox 안의 픽셀은 칠해져 있고 바깥은 흰색이다.
  - PDF 전달과 folio 요청 경로가 맞다.
  - 없는 hit, 사용자 메시지, 다른 대화는 404다.
  - `clip` 변환 버그를 되돌리면 회전 90 케이스가 실패하는 것도 확인했다.
- 전체 단위 테스트 173개가 통과했다.
- 실제 UI 추가 확인: `form002.hwpx`(rhwp, 10쪽)를 `max_tokens: 160`으로 87개 청크로 나눠 적재했다. 2쪽 근거의 "원문 보기"에서 인용 문단 영역만 칠해지고 주변이 함께 잘려 나오는 것을 확인했다(쪽 일부 하이라이트).
- E2E(로컬, 실제 코드):
  - 변환 서버 컨테이너가 `tests/fixtures/contract.docx`를 변환했다. 이후 `folio.parse:default`(heuristic) → `folio.chunk:adaptive` → memory sink 순으로 적재했다.
  - folio API(uvicorn)와 채팅 App(dev 모드)을 띄웠다. LLM은 stub chat 단계를 썼다.
  - Playwright(Chromium)로 질문하고 "원문 보기"를 눌렀다. 데스크톱(1400px)과 모바일(390px, 가로 스크롤 없음) 모두 하이라이트 이미지가 나왔고 콘솔 오류는 없었다. 줄 id 수정 후 근거 본문은 `제1조 목적: 본 계약은 부품 공급 조건을 정한다.`로 나왔다.

## 미확인·남은 일

- 큰 표를 행 단위로 나눈 청크는 bbox가 표 전체다. `form002.hwpx` 1쪽처럼 쪽 대부분이 표이면 쪽 대부분이 칠해진다. 행 단위 bbox는 청크 레코드에 넣지 않았다.
- 청크 하나에 사각형 하나다. 청크가 쪽 전체이면 쪽 전체가 칠해진다. 여러 쪽에 걸친 청크는 첫 쪽만 표시한다. 문장 단위 하이라이트는 블록별 bbox를 청크 레코드에 넣어야 한다.
- 실제 검색 저장소(OpenSearch 등), S3 저장소, 실제 IdP와 함께 확인하지 않았다.
- 큰 PDF는 근거를 볼 때마다 folio에서 전체 PDF를 받는다. 캐시는 브라우저 `Cache-Control: private, max-age=600`뿐이다.
- 원격 저장소가 없어 로컬 `dev`에서 `feat/chat-highlight` 브랜치·worktree로 작업했다.
