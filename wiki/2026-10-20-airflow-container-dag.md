---
type: concept
title: Airflow 예시 DAG를 folio 컨테이너 실행 방식으로 전환
description: FR-15 예시 DAG를 PythonOperator에서 DockerOperator·KubernetesPodOperator로 바꾸고 Compose airflow 프로필과 E2E를 추가한 작업 기록
tags: [airflow, fr-15, compose, helm, deploy]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../project-template/dags/folio_ingest.py
  - ../deploy/compose/docker-compose.yml
  - ../deploy/compose/e2e.sh
  - ./2026-10-20-implementation-core-pipeline.md
---

# Airflow 예시 DAG를 folio 컨테이너 실행 방식으로 전환

## 배경

기존 DAG([코어 파이프라인 기록](2026-10-20-implementation-core-pipeline.md))는 PythonOperator로 `folio`를 Airflow 워커 안에서 import했다. 하지만 Airflow 쪽에 folio를 설치하는 의존성·이미지·compose 서비스가 없었다. 그래서 FR-15는 "구현, 미검증"으로 남아 있었다.

## 결정

- Airflow는 스케줄만 맡는다. 실제 작업은 folio 이미지로 `folio run <uri> --pipeline <p> --source-uri <s>`를 실행하는 별도 컨테이너가 한다. Airflow가 고정한 의존성과 folio 의존성이 충돌하지 않는다.
- 실행 방식은 `FOLIO_AIRFLOW_RUNNER`로 고른다.
  - `docker`(기본): DockerOperator. `mount_tmp_dir=False`(Airflow가 컨테이너 안에서 돌기 때문), `auto_remove="force"`(로그는 task 로그에 남는다), `network_mode=$FOLIO_DOCKER_NETWORK`.
  - `kubernetes`: KubernetesPodOperator. Helm 차트에 새로 추가한 `<fullname>-env` ConfigMap과 Secret을 `env_from`으로 받는다.
- 환경변수는 DAG에 명시한 허용 목록(`FORWARD_ENV`)만 `private_environment`로 넘긴다. 접두사로 긁어 오지 않는다. DB·브로커 URL은 `run_pipeline`에 필요 없어서 제외했다.
- 이전 DAG의 `source_uri` 기능을 유지하려고 `folio run --source-uri`를 추가했다. 빈 문자열은 지정하지 않은 것으로 본다(Airflow param 기본값).
- 실패 처리: 문서가 실패하면 `folio run`이 exit 1을 내고 task도 실패한다. `partial`은 성공으로 처리한다. 이전 DAG도 예외가 나면 실패했으니 동작은 같다.
- 실행되는 컨테이너는 `FOLIO_IMAGE`에 들어 있는 프로젝트(`/app/project`)를 쓴다. Compose의 `FOLIO_PROJECT_DIR` 마운트는 적용되지 않는다. 고객사 파이프라인은 `Dockerfile.project`로 만든 이미지를 쓴다.

## 변경 파일

- `project-template/dags/folio_ingest.py`: DockerOperator / KubernetesPodOperator로 다시 작성. 파라미터는 `uri`, `pipeline`, `source_uri`.
- `folio/cli.py`, `tests/test_cli.py`: `run --source-uri`와 테스트.
- `deploy/compose/docker-compose.yml`: `airflow` 프로필 서비스. `apache/airflow:3.1.0-python3.12`(2.09GB, docker provider 4.4.3, cncf.kubernetes 10.8.1)로 `airflow standalone`을 SQLite로 실행하고, docker.sock을 마운트한다. `group_add: ${DOCKER_GID}`, UI는 `${AIRFLOW_PORT:-8081}`.
- `deploy/compose/e2e.sh`: `AIRFLOW=1` 시나리오. S3 업로드 → `airflow dags test` → OpenSearch에서 해당 `source_uri`의 청크 수가 0보다 큰지 확인.
- `deploy/compose/.env.example`, `deploy/README.md`, `README.md`, `PRD.md`, `project-template/README.md`: 문서.
- `deploy/helm/folio/templates/configmap-env.yaml`: `folio.env`(비밀이 아닌 환경변수)를 ConfigMap으로 렌더링한다.

## 실행 결과 (2026-10-20, 로컬 Docker Desktop/WSL2)

- 원격 저장소가 없어서 로컬 `dev`(`a41fe78`)에서 `feat/airflow-container-dag` 브랜치와 worktree를 만들었다.
- `pytest -q`: 124 passed(worktree 전용 `.venv`).
- `COMPOSE_PROJECT_NAME=folio-airflow-e2e AIRFLOW=1 deploy/compose/e2e.sh`: 통과. API ingest가 succeeded이고, DAG run은 `state=success`(약 6초)였으며, OpenSearch `contracts`에 `source_uri=e2e://airflow/16780` 청크 1개가 적재됐다.
- 실패 경로: 없는 키(`s3://folio-artifacts/nope.pdf`)로 `dags test`를 돌리면 exit 1, task `state=failed`, 결과 JSON은 `NoSuchKey`였다.
- task 로그와 E2E 로그에 MinIO 비밀번호·`AWS_SECRET` 문자열이 나오지 않는 것을 확인했다. 실행 후 남은 folio 컨테이너도 없었다.
- Airflow `/api/v2/monitor/health`: metadatabase·scheduler·triggerer 모두 healthy.
- KubernetesPodOperator: Airflow 이미지 안에서 `FOLIO_AIRFLOW_RUNNER=kubernetes`로 DagBag을 import했을 때 오류가 없었다(`env_from`이 `folio-env`·`folio`로 설정됨). `helm template`/`helm lint`도 통과했다. 실제 클러스터에서 Pod로 실행해 보지는 않았다.
- 문제와 수정: 처음 E2E에서는 `--profile airflow`가 env 파일의 `COMPOSE_PROFILES=infra,cpu`를 덮어써서 MinIO가 뜨지 않았고 `init`이 실패했다. 지금은 `COMPOSE_PROFILES`에 `airflow`를 덧붙이는 방식이다.

## 미완료와 한계

- KubernetesPodOperator 경로는 미검증이다(kind + Airflow 미실행). Pod에 프로젝트 ConfigMap을 마운트하지 않으므로 프로젝트가 들어 있는 이미지가 필요하다.
- Compose Airflow는 개발용이다. SQLite를 쓰는 standalone이고, docker.sock 마운트는 호스트 root 권한과 같다. 운영 환경은 기존 Airflow 클러스터에 DAG만 배포하는 것을 전제로 한다.
- 오프라인 번들(`make_bundle.sh`)에는 Airflow 이미지가 들어 있지 않다.
