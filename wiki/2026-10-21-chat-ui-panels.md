---
type: concept
title: 채팅 UI 패널과 검색 안내 개선
description: 대화·근거 패널 접기, 모바일 서랍 접근성, 검색 대상과 질문 예시, 근거 상세 정보와 설정 문구 개선 기록
tags: [app, chat-ui, accessibility, responsive]
status: draft
generated: 2026-10-21
sources:
  - ../apps/chat/static/index.html
  - ../apps/chat/main.py
  - ../apps/chat/settings.py
  - ../README.md
  - ../PRD.md
  - ./2026-10-21-chat-app-sso.md
---

# 채팅 UI 패널과 검색 안내 개선

## 작업 기준과 결정

- 원격 저장소가 없어 로컬 `dev`를 기준으로 별도 worktree와 `feat/chat-panel-toggle` 브랜치에서 작업했다. 원격 push·공개 배포는 수행하지 않았다.
- 기존 [참조 채팅 App](2026-10-21-chat-app-sso.md)의 단일 HTML 구조와 백엔드 계약을 유지한다.
- 검색 대상은 선택 가능한 pipeline과 현재 대화에 저장된 pipeline으로 안내한다. 문서 목록·개수나 실제 서비스 연결 상태를 추정하지 않는다. 기본 pipeline 이름은 API에서 제공하지 않으므로 “기본 문서 모음”으로 표시한다.

## 구현 내용

- 데스크톱 대화 목록·근거 문서 패널을 각각 접고 펼치며 선택 상태를 브라우저에 저장한다.
- 1100px 이하 화면에서는 서랍으로 열고 배경 클릭·Escape로 닫는다. 닫힌 영역의 포커스 접근을 차단하고 열기·닫기 시 포커스를 관리한다.
- 검색 대상과 로그인 권한에 따른 검색 안내를 표시한다. 질문 예시는 입력창에 채워 사용자가 수정한 뒤 전송한다.
- 근거 문서의 본문·인용·원문 보기와 검색 상세 정보를 구분하고 점수·메타데이터는 상세 접기에 둔다.
- 검색 설정은 쉬운 용어로 바꾸고 고급 검색 조건은 접어 둔다. 기존 대화의 검색 대상은 고정하며 변경하려면 새 대화를 시작하도록 안내한다.
- 사용 방법은 [README](../README.md), 요구사항은 [PRD FR-32](../PRD.md)에 반영했다.

## 검증 결과

- `git diff --check` 통과.
- Playwright/Chromium에서 실제 HTML과 가상 API 응답으로 확인했다. 외부 서비스는 호출하지 않았다.
  - 좌·우 패널의 독립 접기·펼치기, 양쪽 접기 시 채팅 전체 폭 사용, 새로고침 후 상태 복원.
  - 접힌 근거 패널에서 인용 클릭 시 다시 열림. 모바일에서는 패널로 포커스 이동.
  - 320·390·901·1024·1101·1440px 화면에서 가로 넘침 없음. 901px에서는 채팅 영역이 이전 261px에서 901px로 확장됨.
  - 모바일 배경 클릭·Escape 닫기, Tab 순환, 포커스 복귀, 닫힌 패널과 열린 서랍 뒤 배경의 `inert`, `aria-modal`, 데스크톱 전환 후 상태 복원.
  - 검색 대상 변경, 기존 대화의 저장된 검색 대상 표시·선택기 잠금, 질문 예시의 입력창 채우기.
  - 근거 메타데이터 기본 접힘, 상세 펼침, 동일 문서 여러 조각의 문서 수 중복 제거.
  - 쉬운 검색 방식 레이블을 선택해도 API의 `mode: vector`, `k`, `filters` 값이 유지됨. JSON 오류 시 고급 설정이 열리고 질문 내용이 입력창에 복원됨.
  - 다크 모드 렌더링을 확인했고 브라우저 JavaScript 오류가 없었다.
- 채팅 App 회귀 테스트 11개 통과(건너뜀 없음). 실제 folio API를 memory sink·stub chat 단계로 연결한 기존 테스트도 포함한다.
  - 명령: `uv run --offline --no-project --with pytest --with fastapi --with authlib --with itsdangerous --with httpx --with sqlalchemy --with pymupdf --with pyyaml --with celery --with python-multipart python -m pytest tests/test_chat_app.py -q`
  - 기존 캐시의 의존성으로 실행했으며, FastAPI 테스트 클라이언트의 httpx 관련 사용 중단 경고 1건이 있었다.

## 확인 범위의 한계
- 실제 IdP·실제 LLM·배포 환경의 연결 시험은 이 문서에서 검증 완료로 주장하지 않는다.
