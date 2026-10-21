---
type: concept
title: Airflow 메타데이터 DB를 folio 내장 PostgreSQL과 공유
description: 외부 DB 없이 folio 차트의 내장 PostgreSQL에 Airflow 메타데이터 DB를 별도 DB·계정으로 두는 차트 기능(extraDatabases)과 k3s 설치 검증 기록
tags: [airflow, postgres, helm, fr-15, deploy]
status: draft
generated: 2026-10-21
sources:
  - ../deploy/helm/folio/values.yaml
  - ../deploy/helm/folio/templates/_helpers.tpl
  - ../deploy/airflow/README.md
  - ./2026-10-20-airflow-container-dag.md
---

# Airflow 메타데이터 DB를 folio 내장 PostgreSQL과 공유

## 결정

- 사용자 방침은 외부 종속성을 최소화하는 것이다. 그래서 운영 Airflow의 메타데이터 DB로 folio 차트의 내장 PostgreSQL을 쓸 수 있게 했다. 외부 PostgreSQL을 쓰는 방식도 그대로 지원한다.
- 같은 서버에 별도 DB와 owner 계정을 둔다. `airflow` 계정은 자기 DB만 소유하고 `folio` DB에는 쓸 수 없다.
- 전제 조건: `infra.postgres.persistence.enabled=true`(기본값 emptyDir는 재시작 시 데이터 손실)와 백업(PVC 스냅샷 또는 `pg_dump`). 단일 replica라 postgres가 장애 나면 folio와 Airflow가 함께 멈춘다. 이 위험은 수용한다.
- 잃어도 피해가 작다고 본 근거: Airflow DB에는 실행 이력·상태·connection·variable만 있다. DAG는 git에, 산출물은 S3·OpenSearch에 있다.

## 변경

- `values.yaml`에 `infra.postgres.extraDatabases: [{name, user, password}]`를 추가했다. 비밀번호는 Secret 키 `POSTGRES_<NAME>_PASSWORD`에 둔다(`secrets.create`이면 `password`에서, `existingSecret`이면 사용자가 넣는다).
- `_helpers.tpl`:
  - `folio.extraDbKey`: 이름과 계정이 SQL 식별자 규칙(`^[a-z_][a-z0-9_]*$`)을 따르는지 검사한다.
  - `secretData`에 비밀번호를 추가한다(`secrets.create`이면 필수).
  - init Job의 initContainer `create-databases`: `wait-for-infra` 다음에 `postgres` 이미지의 `psql`로 role이 없으면 만들고, 비밀번호를 다시 설정하고, DB가 없으면 만든다. 비밀번호는 psql 변수(`:'pw'`)로 넘겨 SQL 인젝션과 따옴표 문제를 피한다. hook 모드는 내장 인프라와 함께 쓸 수 없으므로(기존 제약) job 모드에서만 실행된다.
- 문서: `deploy/airflow/README.md`에 "Share folio's PostgreSQL" 절을, `values-official-chart.yaml`과 차트 README에 안내를, PRD 메타 DB 행과 README에 공유 옵션을 추가했다.

## 검증 결과

- `helm lint`를 통과했고, `helm template`(values·dev·gpu·offline)도 문제없었다. 잘못된 이름(`air-flow`)과 비밀번호 누락은 명확한 메시지로 실패했다. `existingSecret`이면 Secret 키를 참조만 하는 것도 확인했다.
- 스크립트 단독 테스트(DGX Spark docker, `postgres:16`, uid 10001, read-only rootfs):
  - 첫 실행에서 role과 DB를 만들었다. 두 번째 실행은 `ALTER ROLE`만 했다(멱등).
  - 따옴표가 들어간 비밀번호도 동작했다. 이전 비밀번호는 거부됐다.
  - `airflow`는 자기 DB에서 테이블을 만들 수 있었고, `folio` DB에서는 실패했다.
- DGX Spark k3s 설치(2026-10-21):
  - 구성: namespace `folio-aftest`에 folio 차트(dev values + postgres persistence 1Gi + `extraDatabases: airflow`), namespace `airflow-aftest`에 공식 Airflow 차트 1.22.0. 메타데이터 Secret은 `folio-postgres.folio-aftest.svc.cluster.local:5432/airflow`를 가리킨다.
  - init Job 로그: `CREATE ROLE / ALTER ROLE / CREATE DATABASE / database airflow ready`.
  - Airflow는 `helm install --wait` 한 번에 배포됐다(이전 기록의 hook 교착 수정도 함께 확인).
  - 테이블 배치: `airflow` DB에 public 테이블 68개, `folio` DB에는 `jobs`만 있다.
  - DAG run `shared-1`: success.
  - postgres Pod 삭제와 재기동 후에도 run 이력이 유지됐다(PVC). Airflow 스케줄러·api-server는 재시작 0회로 복구됐다. `shared-2`: success.
  - `helm upgrade folio`(init Job revision 2): `create-databases`가 `ALTER ROLE`만 실행했고 기존 데이터가 유지됐다. `shared-3`: success. run은 총 3개.
  - OpenSearch에 `af://shared/1~3` 청크가 각 1개씩 적재됐다.
  - `helm uninstall folio` 후에도 PVC `data-folio-postgres-0`가 남는 것을 확인했고, 정리 단계에서 namespace와 함께 삭제했다.
- 정리: release 2개, namespace 2개, 임시 레지스트리, push한 이미지, `postgres:16`(docker), `~/folio-af-test`, 터널, 테스트용 비밀번호 파일을 삭제했다. 다른 세션 컨테이너는 건드리지 않았다. k3s containerd에 남은 이미지는 sudo가 없어 지우지 못했다.

## Compose 경로 (2026-10-21 추가)

- 결정: 개발용 Compose의 Airflow도 SQLite 대신 Compose 내장 postgres의 `airflow` DB를 쓴다. Helm과 같은 변수명 `POSTGRES_AIRFLOW_PASSWORD`를 쓴다. 외부 DB는 `AIRFLOW_DATABASE_URL`로 지정한다.
- 구현:
  - one-shot `airflow-db` 서비스(`postgres:16`, profile `airflow`)가 Helm `create-databases`와 같은 SQL 3줄을 실행한다. 두 곳에 "keep in sync" 주석을 달았다.
  - 공유 스크립트 파일로 빼지 않은 이유: Helm 템플릿은 차트 밖 파일을 읽을 수 없고, 오프라인 번들 구성을 건드리지 않으려 했다.
  - `airflow`는 `airflow-db` 완료 후에 시작한다. `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`으로 연결한다.
  - interpolation은 `:-`만 쓴다(`:?`를 쓰면 airflow 프로필을 안 쓰는 사람의 `config`도 깨진다). 비밀번호가 없으면 서비스가 명확한 메시지로 실패한다.
- 검증(로컬 Docker Desktop):
  - `docker compose config -q`: 기본, `airflow`, offline 모두 통과했다.
  - `COMPOSE_PROJECT_NAME=folio-airflow-e2e AIRFLOW=1 deploy/compose/e2e.sh`: 통과했다. e2e는 이제 `airflow` DB의 `dag_run`에 해당 run이 있는지도 확인한다.
  - executor `LocalExecutor`, `sql_alchemy_conn`이 `postgres:5432/airflow`를 가리키고, SQLite 파일이 없다.
  - `airflow-db` 재실행: `ALTER ROLE`만 했다. `up --force-recreate airflow` 후에도 run 이력이 유지되고 healthy였다.

## 공식 차트 fernet key (2026-10-21 추가)

- 문제: values가 fernet key를 고정하지 않았다. 차트가 설치할 때마다 새 키를 만들면, 남아 있는 공유 DB나 복원한 백업의 connection·variable을 복호화하지 못한다.
- 수정: `fernetKeySecretName: airflow-fernet-key`, 가이드에 Secret 생성 명령과 "키 사본 보관"을 추가했다. 이 수정은 렌더링만 확인했다.

## 미완료

- 백업은 선택형 CronJob으로 추가했다([PostgreSQL 백업 기록](2026-10-21-postgres-backup.md)).
- 연결 수와 부하는 측정하지 않았다(DAG run 3회 규모).
