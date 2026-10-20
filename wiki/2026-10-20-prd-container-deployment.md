---
type: concept
title: PRD에 컨테이너 기반 배포(Docker·Kubernetes) 요구사항 추가
description: PRD.md에 Docker Compose와 Kubernetes(Helm) 배포 지원 요구사항을 추가한 작업 기록
tags: [prd, deploy, docker, kubernetes, helm]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
---

# PRD에 컨테이너 기반 배포 요구사항 추가

## 작업 내역

[PRD.md](../PRD.md)에 다음을 추가했다.

- 목표 6: 같은 이미지로 Docker Compose·Kubernetes(Helm) 배포, 폐쇄망 포함
- 비목표: Operator·CRD, 클러스터·GPU 노드 프로비저닝
- 사용자: 운영 담당
- 기능 요구사항 FR-22~FR-26 (이미지, Compose, Helm, 외부 인프라 선택, 폐쇄망 번들)
- `## 배포` 섹션: 이미지 구성, Compose vs Kubernetes 비교표
- 비기능 요구사항 `배포` 행, 레포 구조 `deploy/` 세분화, 기술 스택 `배포` 행
- 열린 질문: k8s 지원 범위, 이미지·차트 배포 경로

## 결정과 가정

- 결정(제안): API·워커·CLI는 단일 `folio` 이미지를 쓰고 실행 명령으로 역할을 나눈다. 모델 가중치는 이미지에 넣지 않고 볼륨/PVC로 마운트한다.
- 가정: 이미지·Compose·Helm 모두 P0, 외부 인프라 전환과 폐쇄망 번들은 P1. 마일스톤 배치는 확인되지 않았다.

## 검증

- 문서 변경만 수행했다. 코드·차트·이미지는 아직 없으므로 수용 기준은 검증되지 않았다.
- 저장소가 git 저장소가 아니어서 dev 브랜치·worktree 없이 작업 디렉터리에서 직접 수정했다.

## 미완료

- 마일스톤 표(임베디드 콘텐츠)에 배포 항목 반영 여부 확인 필요

## 추가 변경 (2026-10-20): HWPX → PDF 변환기 결정 방식

- 결정: 특정 기존 방식에 맞추지 않고 후보를 샘플로 비교해 가장 나은 방식을 채택한다 (사용자 지시).
- PRD `DOCX · HWPX` 절에 선정 기준(컨테이너·폐쇄망 동작, 변환 품질, 속도·안정성, 라이선스)과 후보 목록을 추가하고, 열린 질문 문구를 수정했다.
- 후보 목록(LibreOffice + H2Orestart 등, 한컴 변환 제품)은 기능·라이선스를 검증하지 않았다.

## 조사 (2026-10-20): HWPX → PDF 변환기 후보

웹 자료 기준 조사이며 실제 샘플 변환은 하지 않았다.

- hwpforge (PyPI 0.16.11, 2026-10-13, MIT/Apache-2.0, Beta): HWPX에 저장된 한컴 레이아웃을 재생한다. 한컴이 저장한 HWPX만 가능하고 폰트가 없으면 기본적으로 실패한다. https://pypi.org/project/hwpforge/
- rhwp (MIT, Rust, pre-1.0, 약 4k stars): 자체 조판 엔진과 CLI `export-pdf`를 제공한다. https://github.com/edwardkim/rhwp
- LibreOffice + H2Orestart (v0.8.0, 2026-10-18, GPL): headless 변환을 지원하고 JRE가 필요하다. https://extensions.libreoffice.org/en/extensions/show/27504
- 한컴 한글 SDK: 상용이며 HWPX → PDF 변환을 지원한다. Linux 지원과 가격은 공개되지 않았다. 구 DocsConverter는 hwpx 처리 누락 사례가 있다. https://www.hancom.com/product/sdk/hwpSdk , https://forum.developer.hancom.com/t/docsconverter/738
- 가정: 1순위 후보는 hwpforge·rhwp(경량, permissive 라이선스), 기준선은 LibreOffice+H2Orestart. 최종 결정은 샘플 벤치마크 후.

## 후속 (2026-10-20)

- 위 "가정"(hwpforge·rhwp 1순위)은 실제 벤치마크로 대체되었다. hwpforge는 샘플 11개 모두 실패해 제외했고, rhwp 기본 + LibreOffice·H2Orestart 대체로 잠정 결정했다. [벤치마크 기록](2026-10-20-hwpx-converter-benchmark.md)
