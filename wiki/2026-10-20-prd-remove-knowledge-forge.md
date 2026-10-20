---
type: concept
title: PRD에서 knowledge-forge 언급 정리
description: PRD.md에서 knowledge-forge와의 관계·비교 서술을 제거한 작업 기록
tags: [prd, scope]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
---

# PRD에서 knowledge-forge 언급 정리

## 작업 내역

[PRD.md](../PRD.md)에서 knowledge-forge를 언급하거나 연관 짓는 서술을 정리했다. folio는 독립 레포로서 자체 범위만 서술한다.

- 개요: "knowledge-forge와 분리된 새 레포" → "새 레포"
- 배경: "별도 레포인 이유"(knowledge-forge와 책임 분리) → "레포의 책임 범위"로 바꾸고 범위 설명만 유지
- 비목표: "knowledge-forge 기능의 이관이나 대체" 항목 삭제
- 열린 질문: 레포 이름 결정의 "(knowledge-forge와 구분)" 삭제

## 검증

- `rg -i "knowledge.forge"`로 저장소 전체(이 문서 제외)에 남은 언급이 없음을 확인했다.
