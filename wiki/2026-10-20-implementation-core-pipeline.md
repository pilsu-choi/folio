---
type: concept
title: folio 코어 파이프라인 구현 (DOCX·HWPX 제외 PRD 전 항목)
description: FR-20/21을 제외한 PRD FR의 구현 내역, 검증 결과, 결정 사항, 남은 작업
tags: [implementation, parse, sinks, service, deploy, helm, compose, e2e]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../README.md
  - ../docs/INTERFACES.md
  - ./2026-10-20-prd-container-deployment.md
---

# folio 코어 파이프라인 구현

사용자 지시에 따라 DOCX·HWPX(FR-20, FR-21)를 보류하고 [PRD.md](../PRD.md)를 구현했다.

## 작업 환경

- git 저장소가 아니어서 `git init`으로 로컬 `dev` 브랜치를 만들었다(원격 없음). 작업은 `feat/core-pipeline` 브랜치의 worktree `../folio-wt/core-pipeline`에서 했다.
- Python 3.12 venv(uv)를 썼다. 계약(`folio/contracts/`)과 내부 인터페이스([docs/INTERFACES.md](../docs/INTERFACES.md))를 먼저 작성했다. 이후 sub-agent 4개가 파일 소유 범위를 나눠 병렬로 작업했다: parse·backends / chunk·embed·sinks·retrieval·eval / pipeline·storage·CLI·service·template / deploy.

## 구현 내역

| 영역 | FR | 위치 |
| --- | --- | --- |
| 계약(Document JSON, Chunk Record, Sink, YAML) + JSON Schema | 계약 명세 | `folio/contracts/` |
| PDF 텍스트 레이어 + 구조 분석 좌표 매칭, 품질 판정 → OCR 폴백, 표, 회전·CropBox, char 매칭, 이미지 입력, VLM 폴백(기본 꺼짐) | FR-01~06 | `folio/parse/` |
| `StructureBackend` + heuristic·docling·ppstructure·http 어댑터, 구조 분석 서버, 벤치마크 | FR-19 | `folio/backends/`, `scripts/bench_backends.py` |
| chunk auto/section/table/page/fixed, 기본 매퍼, HTTP 임베딩 + 개발용 stub | FR-07~09 | `folio/chunk/`, `folio/map.py`, `folio/embed/` |
| OpenSearch·Milvus·pgvector Sink(교체 시맨틱, alias 교체, pgvector는 DDL 없음) | FR-10, 11 | `folio/sinks/` |
| `run_pipeline`, `reprocess`, 로컬·S3 저장소, CLI | FR-12, 13, 16 | `folio/pipeline.py`, `folio/storage.py`, `folio/cli.py` |
| Ingest API, jobs 테이블, Celery 워커 | FR-14 | `service/` |
| Airflow 예시 DAG | FR-15 | `project-template/dags/folio_ingest.py` |
| 하이브리드 검색(RRF) + rerank, tenant_id·acl 필터 | FR-17 | `folio/retrieval/` |
| Testset, Recall@k·MRR·nDCG, 커밋별 비교 | FR-18 | `folio/eval/` |
| 이미지, Compose, Helm, 외부 인프라 전환, 폐쇄망 번들 | FR-22~26 | `deploy/` |

## 검증 결과 (실제 실행)

- 단위 테스트: `pytest -q` → 102개 통과. 모델·외부 서비스 없이 돈다.
- Sink 통합 테스트: OpenSearch 2.19.1, pgvector pg16, Milvus v2.6.25 컨테이너로 13개 통과, 6개 skip. skip은 다른 백엔드 파라미터에서 해당하지 않는 백엔드 전용 테스트다. 재적재 시 개수가 늘지 않고, 오래된 청크가 지워지고, reindex alias가 교체되고, pgvector 테이블 스키마가 변하지 않고, 다른 테넌트 문서가 검색되지 않음을 확인했다.
- 로컬 CLI(OpenSearch + 임베딩 stub):
  - `init`을 두 번 실행하면 `created` → `unchanged`.
  - `run`을 두 번 실행해도 개수는 14로 그대로다.
  - 필드를 추가하면 `mapping_updated`.
  - `reprocess --from map`은 구조 분석·OCR 호출 0회로 문서 14개 모두에 매퍼 변경을 반영했다.
  - `--reindex`는 alias를 `contracts-v1`에서 `-v2`로 옮기고 개수 14를 유지했다.
  - `eval testset/run/compare`가 돈다.
- Compose E2E(`deploy/compose/e2e.sh`): 시나리오 3 통과.
  - 같은 파일을 다시 올려도 개수가 늘지 않는다.
  - mixed.pdf는 구조 분석 서버의 OCR(rapidocr)을 거쳐 `succeeded`.
  - corrupt.pdf는 `failed`이고 `ParseError` 메시지를 조회할 수 있다.
- kind + Helm E2E(`deploy/kind/e2e.sh`): 설치 후 시나리오 3 통과. 진행 중인 job 5개가 `helm upgrade`(워커 파드 교체)를 거쳐 모두 `succeeded`.
  - 이전 워커가 warm shutdown 중에 끝까지 처리했다.
  - 별도 실행에서 job 2개가 `parse` 중일 때 워커 파드를 강제 삭제(`--grace-period=0 --force`)했다. 재전달되어 job 4개 모두 `succeeded`(acks_late + reject_on_worker_lost).
- 폐쇄망 번들: `make_bundle.sh`가 1.9 GB tar.gz를 만들었다. `--network none` Docker-in-Docker 안에서 `install.sh`가 이미지를 로드하고 Compose를 올렸고, 업로드가 `succeeded`.
- 이미지: `folio:0.1.0` 654 MB와 `folio-structure:0.1.0-cpu`(heuristic) 757 MB는 둘 다 uid 10001로 실행된다. `folio-opensearch:0.1.0`(nori 포함)은 1.46 GB.
- `helm lint`·`helm template`이 default, dev, gpu, offline values에서 통과. `docker compose config` 통과.

### 구조 분석 백엔드 벤치마크 (픽스처, CPU)

| 백엔드 | 디지털 텍스트 일치 | 표(행·열, 셀, span) | OCR 유사도 | 초/페이지 |
| --- | --- | --- | --- | --- |
| heuristic | 1.000 | 일치 | 0.72 (rapidocr) | 0.04 (OCR 포함 0.31) |
| docling 2.140 | 1.000 | 일치 | 0.75 | 1.75 |
| ppstructure | 1.000 | 일치 | 0.91 | 20.6 (모델 로드 포함) |

합성 픽스처(`tests/fixtures/`)만 썼다. 한글이 들어간 고객사 샘플이 필요해서 기본 백엔드는 아직 정하지 않았다.

다운로드 기록:

| 항목 | 크기 |
| --- | --- |
| `.venv-bench` (docling) | 1.7 GB |
| HF 모델 캐시 | 약 506 MB |
| `.venv-bench-paddle` | 1.6 GB |
| `~/.paddlex` 모델 | 847 MB |

## 결정과 가정

- 결정: `StructureBackend.analyze` 입출력 좌표계는 페이지 이미지 픽셀이다. parse가 pt로 되돌린다.
- 결정: `backend: auto`는 `FOLIO_STRUCTURE_URL`이 있으면 HTTP 구조 분석 서버, 없으면 모델 없는 heuristic 백엔드를 고른다.
- 결정: API로 올린 문서의 `doc_id`는 `upload://{pipeline}/{filename}`과 content hash로 만든다. 그래서 같은 파일 재업로드가 멱등이다.
- 결정: `minio/minio`를 Docker Hub·quay에서 더 이상 받을 수 없어 digest로 고정한 `cgr.dev/chainguard/minio`로 바꿨다. `docker load`가 digest를 버리므로, 폐쇄망 번들은 digest를 확인한 뒤 태그로 저장한다.
- 결정: Helm 초기화는 pre-install hook이 아니라 revision 접미사가 붙은 일반 Job이다. 내장 인프라와 ConfigMap이 먼저 있어야 하기 때문이다. 외부 인프라 배포는 `init.mode=hook`을 고를 수 있다.
- 결정: 워커 동시성은 `FOLIO_WORKER_CONCURRENCY`로 정하고 기본값은 2다.
- 가정: 개발용 임베딩은 해시 기반 stub(`folio embed-stub`, 384차원)이다. 운영에는 TEI나 vLLM 같은 실제 OpenAI 호환 엔드포인트가 필요하다.

## 미완료·미검증

- DOCX·HWPX(FR-20, FR-21): 보류.
- 작업 중 dev에 PRD 개정(형식·VLM·청킹)이 들어왔다. 병합한 뒤 확인한 개정 PRD와의 차이는 다음과 같다. 이번 작업에서 반영하지 않았다.
  - FR-27~29(PPTX·XLSX·Markdown·TXT): 미구현.
  - FR-02·05·06: 지금은 이미지·품질 미달 페이지가 OCR 경로가 기본이고, VLM은 옵션이다. 개정 PRD는 VLM을 기본으로 둔다.
  - FR-07: 지금은 `auto` 전략이다. 개정 PRD는 `adaptive`를 기본으로 둔다.
- 미검증:
  - FR-15 Airflow DAG(로컬 Airflow로 실행하지 않음).
  - docling·ppstructure·GPU 구조 분석 백엔드로 실제 이미지 빌드.
  - 사설 레지스트리를 거치는 Helm 폐쇄망 경로.
  - FR-25 외부 인프라 전환.
  - CI 워크플로(`.github/workflows/ci.yml`).
  - 이미지 취약점 스캔.
  - 한국어 nori 분석기 자체. 통합 테스트는 standard 분석기를 썼다.
- 알려진 한계:
  - 읽기 순서가 단순 y→x 정렬이라 다단 문서는 섞인다.
  - heuristic 백엔드의 표 검출은 괘선이 있어야 한다.
  - rapidocr 기본 모델은 한글에 약하다.
  - pgvector 키워드 검색은 `simple` tsvector를 쓴다.
- ruff: 의도적인 broad `except`(페이지 단위 격리, readiness)가 일부 남아 있다.
