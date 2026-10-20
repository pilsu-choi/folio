---
type: concept
title: README 시각화 개편
description: README를 Mermaid 다이어그램과 쉬운 용어 중심으로 다시 쓴 작업 기록과 검증 결과
tags: [docs, readme, mermaid]
status: draft
generated: 2026-10-21
sources:
  - ../README.md
  - ../PRD.md
---

# README 시각화 개편

## 작업

- 브랜치 `docs/readme-visual`, worktree `../folio-wt/readme-visual`. 원격이 없어 로컬 `dev`(0d0ddf5)에서 분기했다.
- [README.md](../README.md)를 다시 썼다. 기능·명령·환경변수는 바꾸지 않았고 배치와 표현만 바꿨다.
  - Mermaid 다이어그램 7개: 전체 흐름, 구축 파일 3개, 적재 시퀀스(API → RabbitMQ → 워커), 파싱 판단, 검색 흐름, 재처리, 배포.
  - 설치 옵션 전체, 파이프라인 YAML 전체, API·실행 모드·평가는 `<details>`로 접었다.
  - 현재 상태를 표로 정리하고 용어집을 추가했다.
- 다이어그램 내용은 코드 기준으로 맞췄다: `folio/parse/core.py`(페이지 품질 → VLM/OCR 폴백, 그림·약한 표 VLM 보강), `service/api.py`·`service/worker.py`(job 생성 → 원본 저장 → Celery 등록), `folio/retrieval/base.py`(BM25 + 벡터 → RRF → rerank → k), `folio/cli.py`(`reprocess --from parse|chunk|map`), `deploy/compose/docker-compose.yml`(프로필).
- PRD는 기능 변경이 없어 수정하지 않았다.

## 검증

- `@mermaid-js/mermaid-cli@11.4.2`로 README의 Mermaid 블록 7개를 PNG로 렌더링했다. 모두 오류 없이 렌더링됐고 한글 라벨을 눈으로 확인했다.
- README의 상대 링크 9개가 모두 존재함을 `test -e`로 확인했다.
- 이전 README의 명령·환경변수·엔드포인트·프로필 키워드가 새 README에 남아 있는지 grep으로 확인했다.
- GitHub 등 실제 호스팅 화면에서의 렌더링은 확인하지 않았다.
