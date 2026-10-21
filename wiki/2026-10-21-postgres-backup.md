---
type: concept
title: 내장 PostgreSQL 선택형 백업 (backup.enabled)
description: folio 차트 내장 PostgreSQL의 folio·extra DB를 pg_dump CronJob으로 PVC(NAS) 또는 S3에 백업하는 기능과 k3s 백업·복원 검증 기록
tags: [helm, postgres, backup, airflow, deploy]
status: draft
generated: 2026-10-21
sources:
  - ../deploy/helm/folio/templates/cronjob-backup.yaml
  - ../deploy/helm/folio/templates/_helpers.tpl
  - ../deploy/helm/folio/README.md
  - ./2026-10-21-airflow-shared-postgres.md
---

# 내장 PostgreSQL 선택형 백업 (backup.enabled)

## 결정 (사용자)

- 기본값은 `backup.enabled: false`다.
- 저장 위치는 두 가지 중에서 고른다. 기본은 `pvc`(NAS/NFS PVC를 `existingClaim`으로 지정 가능)이고, 선택지로 `s3`(`FOLIO_STORAGE_URI` bucket)가 있다.
- 주기는 매일 03:00(`timeZone: Asia/Seoul`), 보관은 7개다.
- 대상은 내장 PostgreSQL의 folio DB와 `infra.postgres.extraDatabases`(예: airflow)다. 외부 DB에 켜면 렌더링이 실패한다(외부 DB는 자체 백업을 쓴다).

## 구현

- `templates/cronjob-backup.yaml`: CronJob(`concurrencyPolicy: Forbid`). 기존 `postgres` 이미지와 folio 이미지만 쓰고 새 이미지는 없다.
  - `pvc`: `dump` 컨테이너가 `pg_dump -Fc`로 `<UTC ts>.partial/`에 쓴 뒤 `<ts>/`로 rename한다. 이후 `[0-9]*Z` 디렉터리만 대상으로 오래된 것을 지운다. 공유 NAS claim의 다른 데이터나 `lost+found`는 건드리지 않는다.
  - `s3`: `dump` initContainer가 emptyDir에 dump를 만들고, folio 이미지의 `upload` 컨테이너가 `folio.storage` S3 클라이언트로 `upload_file`(스트리밍) 업로드 후 오래된 세트를 지운다.
  - 차트가 만든 PVC에는 `helm.sh/resource-policy: keep`을 달았다.
- init Job initContainer `check-backup-volume`: 설치·업그레이드 때 백업 PVC에 쓰기 테스트를 한다.
- `values.yaml`의 `backup.*`와 차트 README에 백업·복원 절차를 추가했다. Airflow 가이드와 PRD에서 링크한다.

## 검증 (2026-10-21, DGX Spark k3s, local-path)

- `helm lint`, `helm template`(values·dev·gpu·offline 모두 CronJob 0개, 즉 기본 꺼짐)를 확인했다. pvc, s3, existingClaim 세 변형을 렌더링했고, 외부 DB에 켜거나 target이 잘못되면 실패 메시지가 나왔다.
- 처음 설치는 실패했다. 차트 PVC가 WaitForFirstConsumer라 첫 CronJob 실행 전까지 Pending이었고, `helm install --wait`가 `context deadline exceeded`로 끝났다. 그래서 `check-backup-volume`를 추가했다. 재설치하니 PVC가 Bound됐고, `backup volume writable` 로그와 함께 정상 배포됐다.
- pvc target(retention 2):
  - `kubectl create job --from=cronjob/folio-backup`을 3회 실행했다. `folio`·`airflow` dump가 생성됐고 가장 오래된 세트 1개가 pruned됐다. PVC에는 2세트가 남았다.
  - 복원: airflow DB의 marker 테이블을 지운 뒤 `pg_restore --clean --if-exists`로 복원하니 marker가 돌아왔다. 새 DB(`airflow_fresh`)에 복원해도 marker가 있었다. `folio.dump`에는 `jobs` 테이블이 들어 있었다.
- s3 target(`helm upgrade --set backup.target=s3`): 3회 실행 후 MinIO `_backups/postgres/`에 2세트가 남았다. 내려받은 `airflow.dump`는 `PGDMP` 헤더가 있었고, `pg_restore --list`에 marker 데이터가 있었다. s3에서 내려받아 DB에 복원하는 것까지는 하지 않았다(pvc 경로로 복원을 확인했고, 같은 dump 형식이다).
- `helm uninstall` 후에도 `data-folio-postgres-0`와 `folio-backup` PVC가 남는 것을 확인했다.
- 정리: release, namespace, 임시 레지스트리, push한 이미지, `~/folio-bk-test`, 터널, 로컬 임시 파일을 삭제했다. 다른 세션 컨테이너는 건드리지 않았다.

## 미완료와 한계

- NAS/NFS 실환경(root_squash, uid 10001 권한)은 미검증이다. local-path로만 확인했다.
- 실제 03:00 스케줄 실행은 기다리지 않았다. CronJob의 `schedule`·`timeZone` 필드만 확인했다.
- dump 파일 암호화와 클러스터 밖으로의 복제는 범위 밖이다.
- Compose 내장 postgres 백업은 넣지 않았다(Compose는 개발용).
