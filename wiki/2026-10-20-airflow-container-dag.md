---
type: concept
title: Airflow 예시 DAG를 folio 컨테이너 실행 방식으로 전환
description: FR-15 예시 DAG를 DockerOperator·KubernetesPodOperator로 바꾸고 Compose airflow 프로필, k3s KPO 검증, 운영 Airflow(공식 차트) 가이드를 추가한 작업 기록
tags: [airflow, fr-15, compose, helm, deploy]
status: draft
generated: 2026-10-20
sources:
  - ../PRD.md
  - ../project-template/dags/folio_ingest.py
  - ../deploy/compose/docker-compose.yml
  - ../deploy/compose/e2e.sh
  - ./2026-10-20-implementation-core-pipeline.md
  - ../deploy/airflow/README.md
  - https://airflow.apache.org/docs/helm-chart/
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
- 빈 `uri`: `folio run ""`은 작업 디렉터리(`/app`) 전체를 처리하게 된다. 그래서 CLI가 exit 2로 거부하고, DAG param `uri`는 `minLength=1`로 필수다(트리거 시점에 Airflow가 거부).
- 실패 처리: 문서가 실패하면 `folio run`이 exit 1을 내고 task도 실패한다. `partial`은 성공으로 처리한다. 이전 DAG도 예외가 나면 실패했으니 동작은 같다.
- 실행되는 컨테이너는 `FOLIO_IMAGE`에 들어 있는 프로젝트(`/app/project`)를 쓴다. Compose의 `FOLIO_PROJECT_DIR` 마운트는 적용되지 않는다. 고객사 파이프라인은 `Dockerfile.project`로 만든 이미지를 쓴다.

## 변경 파일

- `project-template/dags/folio_ingest.py`: DockerOperator / KubernetesPodOperator로 다시 작성. 파라미터는 `uri`, `pipeline`, `source_uri`.
- `folio/cli.py`, `tests/test_cli.py`: `run --source-uri`와 테스트.
- `deploy/compose/docker-compose.yml`: `airflow` 프로필 서비스. `apache/airflow:3.1.0-python3.12`(2.09GB, docker provider 4.4.3, cncf.kubernetes 10.8.1)로 `airflow standalone`을 SQLite로 실행하고, docker.sock을 마운트한다. `group_add: ${DOCKER_GID}`, UI는 `${AIRFLOW_PORT:-8081}`.
- `deploy/compose/e2e.sh`: `AIRFLOW=1` 시나리오. S3 업로드 → `airflow dags trigger`(스케줄러·LocalExecutor 경로) → `list-runs`로 `success`까지 대기 → OpenSearch에서 해당 `source_uri`의 청크 수가 0보다 큰지 확인. Compose의 Airflow는 `DAGS_ARE_PAUSED_AT_CREATION=False`라 DAG가 unpause 상태로 생성된다.
- `deploy/compose/.env.example`, `deploy/README.md`, `README.md`, `PRD.md`, `project-template/README.md`: 문서.
- `deploy/helm/folio/templates/configmap-env.yaml`: `folio.env`(비밀이 아닌 환경변수)를 ConfigMap으로 렌더링한다.

## 실행 결과 (2026-10-20, 로컬 Docker Desktop/WSL2)

- 원격 저장소가 없어서 로컬 `dev`(`a41fe78`)에서 `feat/airflow-container-dag` 브랜치와 worktree를 만들었다.
- `pytest -q`: 124 passed(worktree 전용 `.venv`).
- `COMPOSE_PROJECT_NAME=folio-airflow-e2e AIRFLOW=1 deploy/compose/e2e.sh`: 통과. `airflow dags test`(in-process) 버전과, 스케줄러가 실행하는 `dags trigger` 버전 모두에서 확인했다. 마지막 실행은 새 스택에서 API ingest succeeded, DAG run `e2e-21918` success, OpenSearch `contracts`에 `source_uri=e2e://airflow/24123` 청크 1개 적재였다.
- 기본 설정(paused at creation)에서는 트리거한 run이 시작되지 않는다. 그래서 dev compose에서 unpause 기본값을 켰다. 수동 unpause 후 `dags trigger`로도 success와 청크 적재를 확인했다.
- XCom: task 로그에서 컨테이너의 마지막 출력 줄(결과 JSON, `status: succeeded`) 바로 다음이 `Pushing xcom`인 것을 확인했다.
- `uri` 없이 `dags trigger`를 하면 param 검증으로 거부된다(exit 1). CLI의 빈 경로 거부는 단위 테스트로 확인했다.
- 실패 경로: 없는 키(`s3://folio-artifacts/nope.pdf`)로 `dags test`를 돌리면 exit 1, task `state=failed`, 결과 JSON은 `NoSuchKey`였다.
- task 로그와 E2E 로그에 MinIO 비밀번호·`AWS_SECRET` 문자열이 나오지 않는 것을 확인했다. 실행 후 남은 folio 컨테이너도 없었다.
- Airflow `/api/v2/monitor/health`: metadatabase·scheduler·triggerer 모두 healthy.
- KubernetesPodOperator: Airflow 이미지 안에서 `FOLIO_AIRFLOW_RUNNER=kubernetes`로 DagBag을 import했을 때 오류가 없었다(`env_from`이 `folio-env`·`folio`로 설정됨). `helm template`(values·dev·gpu·offline)과 `helm lint`도 통과했다. 실제 클러스터에서 Pod로 실행해 보지는 않았다.
- 문제와 수정: 처음 E2E에서는 `--profile airflow`가 env 파일의 `COMPOSE_PROFILES=infra,cpu`를 덮어써서 MinIO가 뜨지 않았고 `init`이 실패했다. 지금은 `COMPOSE_PROFILES`에 `airflow`를 덧붙이는 방식이다.

## KubernetesPodOperator 실행 (2026-10-20, pgx spark k3s)

- 환경: pgx spark(aarch64, GB10), 단일 노드 k3s v1.36.5+k3s1. sudo 없이 진행하려고 pgx docker에 임시 레지스트리 `registry:2`(`127.0.0.1:5000`)를 띄웠다. k3s containerd는 `registries.yaml` 없이도 `localhost:5000`에서 이미지를 받았다(smoke pod로 먼저 확인).
- 이미지(arm64, pgx에서 빌드): `folio:0.1.0` 696MB, `folio-opensearch:0.1.0` 1.18GB. 추가 다운로드: `apache/airflow:3.1.0-python3.12` 2.11GB, `registry:2`. postgres·rabbitmq·minio는 k3s가 인터넷에서 직접 받았다.
- folio 차트: 로컬 helm에서 SSH 터널(16443→6443)로 `helm install folio -n folio-kpo -f values-dev.yaml --set structure.enabled=false --set vlmStub.enabled=false`를 실행했다. 이미지는 `image.repository`·`infra.opensearch.image.repository` override로 지정했다. FR-26 `image.registry` 경로는 검증하지 않았다.
- Airflow: pgx에서 `docker run --network host apache/airflow:3.1.0-python3.12`로 `airflow db migrate && airflow dags test folio_ingest`를 실행했다(`FOLIO_AIRFLOW_RUNNER=kubernetes`, kubeconfig는 env로 전달). 스케줄러 경로는 Compose에서 이미 검증했으므로 여기서는 `dags test`만 했다.
- 결과:
  - 성공 케이스: exit 0, `state=success`. 결과 JSON `status: succeeded`, OpenSearch에 `source_uri=kpo://pgx/ok` 청크 1개. 성공한 Pod는 삭제됐다.
  - 실패 케이스(없는 키): exit 1, `state=failed`, `NoSuchKey`. 실패한 Pod(`run-pipeline-*`, Error)는 디버깅용으로 남았다(`delete_succeeded_pod`).
  - 로그에 dev MinIO 비밀번호가 나오지 않았다. `kubernetes_default` connection 없음 오류 로그는 kubeconfig로 폴백하면서 나는 것이라 무해하다.
- 정리: namespace `folio-kpo` 삭제, 레지스트리 컨테이너·push한 이미지·Airflow 이미지·`~/folio-kpo-test` 삭제, 터널과 로컬 kubeconfig 복사본 삭제. pgx의 다른 세션 컨테이너(m1-vlm, rerank, embed)와 crash 중인 nvidia device plugin은 건드리지 않았다.
- 실행 전에 확인한 것: KPO `do_xcom_push` 기본값은 False라 xcom sidecar가 필요 없다. kubernetes runner의 결과는 task 로그에만 남는다.

## 운영 Airflow 범위 결정 (2026-10-20)

- 결정(사용자 요청): 운영 Airflow 자체는 설치하지 않는다. 대신 공식 Apache Airflow Helm 차트 기준의 설치 가이드와 values 예시, folio namespace용 RBAC를 제공한다. 위치는 [deploy/airflow/README.md](../deploy/airflow/README.md)이고 PRD 기술 스택 표에 반영했다.
- `values-official-chart.yaml`: 차트 1.22.0(Airflow 3.2.2), KubernetesExecutor, FOLIO env, git-sync DAG, 외부 PostgreSQL(`data.metadataSecretName`), Redis 끔, API·JWT secret 이름 고정.
- `rbac-folio-namespace.yaml`: Role(pods create/get/list/watch/delete/patch, pods/log get, events list/watch)과 RoleBinding(subject `airflow/airflow-worker`).
- 검증: `helm template airflow apache-airflow/airflow --version 1.22.0 -f values-official-chart.yaml` 렌더링이 성공했다. worker SA 이름이 `airflow-worker`이고, task pod template에 `FOLIO_*` env가 들어가는 것을 확인했다. 차트 기본 이미지 `apache/airflow:3.2.2`(2.27GB, 확인 후 삭제)에서 DAG import도 확인했다. kubernetes·docker runner 모두 `import_errors` 없음. 실제 설치·실행은 하지 않았다(미검증).

## 공식 Airflow 차트 설치 검증 (2026-10-20, pgx spark k3s)

- 구성: namespace `folio-aftest`에 folio 차트(dev values, structure·vlm stub 끔), namespace `airflow-aftest`에 공식 차트 `apache-airflow/airflow` 1.22.0(Airflow 3.2.2)을 설치했다. values는 `deploy/airflow/values-official-chart.yaml`이고, 테스트용 override는 FOLIO env와 git-sync repo뿐이다.
- 테스트용 대체물: "외부" PostgreSQL로 `postgres:16` Deployment를, git-sync 원격으로 `alpine/git:2.47.2` + `git-daemon` 패키지를 쓰는 git 서버(DAG 파일 하나를 담은 repo)를 띄웠다. 이미지는 pgx 임시 레지스트리(`localhost:5000`)에 다시 빌드해서 올렸다.
- 문제와 수정: 처음 `helm install --wait`가 멈췄다. 공식 차트의 migration·create-user Job은 post-install hook인데, `--wait`가 끝날 때까지 hook이 시작되지 않고 파드는 `wait-for-airflow-migrations`에서 CrashLoop했다. values에 `migrateDatabaseJob/createUserJob.useHelmHooks: false`를 추가하고 `helm upgrade`했더니 정상 배포됐다.
- 결과:
  - git-sync로 `folio_ingest`가 로드됐다(`/opt/airflow/dags/repo/dags/folio_ingest.py`, import 오류 없음). 기본값대로 paused 상태로 생성됐다.
  - RBAC: `airflow-aftest:airflow-worker`는 `folio-aftest`에서 pods create/delete, pods/log get, events list가 yes, `default` namespace의 pods create는 no다.
  - unpause 후 `dags trigger`: `af-ok`는 success. KubernetesExecutor 워커 Pod가 클러스터 내부 인증으로 `folio-aftest`에 KPO Pod를 만들었고, OpenSearch에 `source_uri=af://pgx/official-chart` 청크 1개가 적재됐다. `af-fail`(없는 키)은 failed이고, folio Pod는 Error 상태로 남았다. 워커 Pod는 모두 정리됐다.
  - task 로그: 원격 로그·로그 PVC를 설정하지 않으면 워커 Pod와 함께 사라진다. 가이드와 values에 원격 로그 설정 예시를 주석으로 넣었다(미검증).
- 정리: helm release 2개와 namespace 2개, 레지스트리 컨테이너, push한 이미지, `~/folio-af-test`, 터널, kubeconfig 복사본을 삭제했다. k3s containerd에 남은 이미지(`apache/airflow:3.2.2`, git-sync, postgres 등)는 sudo가 없어 지우지 못했다.

## 미완료와 한계

- 운영 Airflow 가이드의 원격 로그 설정, 외부 PostgreSQL 실서버, git-sync 인증(credentialsSecret)은 미검증이다.
- 다중 노드 클러스터에서는 검증하지 않았다(단일 노드 k3s만). KPO Pod에는 프로젝트 ConfigMap을 마운트하지 않으므로 프로젝트가 들어 있는 이미지가 필요하다.
- Compose Airflow는 개발용이다. docker.sock 마운트는 호스트 root 권한과 같다. 메타데이터 DB는 2026-10-21에 SQLite에서 내장 postgres로 바꿨다([공유 PostgreSQL 기록](2026-10-21-airflow-shared-postgres.md)).
- 오프라인 번들(`make_bundle.sh`)에는 Airflow 이미지가 들어 있지 않다.
