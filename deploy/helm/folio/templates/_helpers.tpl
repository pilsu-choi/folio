{{- define "folio.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "folio.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}{{- end -}}
{{- end -}}
{{- end -}}

{{- define "folio.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "folio.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* selector labels: call with (dict "root" . "component" "api") */}}
{{- define "folio.selector" -}}
app.kubernetes.io/name: {{ include "folio.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* image reference: (dict "root" . "repository" x "tag" y) ; registry prefix is global */}}
{{- define "folio.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- if $reg }}{{ trimSuffix "/" $reg }}/{{ end }}{{ .repository }}:{{ .tag }}
{{- end -}}

{{- define "folio.appImage" -}}
{{- include "folio.image" (dict "root" . "repository" .Values.image.repository "tag" (default .Chart.AppVersion .Values.image.tag)) -}}
{{- end -}}

{{- define "folio.structureImage" -}}
{{- $tag := .Values.structure.image.tag | default (printf "%s-%s" .Chart.AppVersion (ternary "gpu" "cpu" .Values.structure.gpu.enabled)) -}}
{{- include "folio.image" (dict "root" . "repository" .Values.structure.image.repository "tag" $tag) -}}
{{- end -}}

{{- define "folio.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}{{ default (include "folio.fullname" .) .Values.serviceAccount.name }}
{{- else -}}{{ default "default" .Values.serviceAccount.name }}{{- end -}}
{{- end -}}

{{- define "folio.secretName" -}}
{{- if .Values.secrets.existingSecret -}}{{ .Values.secrets.existingSecret }}
{{- else if .Values.secrets.create -}}{{ include "folio.fullname" . }}
{{- else -}}{{ fail "set secrets.existingSecret, or secrets.create=true with secrets.values" }}{{- end -}}
{{- end -}}

{{- define "folio.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* pre-install hook annotations for config objects when init.mode=hook */}}
{{- define "folio.preHook" -}}
{{- if and .Values.init.enabled (eq .Values.init.mode "hook") }}
annotations:
  "helm.sh/hook": pre-install,pre-upgrade
  "helm.sh/hook-weight": "-10"
  "helm.sh/hook-delete-policy": before-hook-creation
{{- end }}
{{- end -}}

{{- define "folio.anyBundled" -}}
{{- if or .Values.infra.postgres.enabled .Values.infra.rabbitmq.enabled .Values.infra.opensearch.enabled .Values.infra.minio.enabled .Values.infra.milvus.enabled -}}true{{- end -}}
{{- end -}}

{{/* secret content: bundled-infra derived values overridden by secrets.values */}}
{{- define "folio.secretData" -}}
{{- $fn := include "folio.fullname" . -}}
{{- $d := dict -}}
{{- with .Values.infra.postgres }}{{- if .enabled }}
{{- $pw := required "infra.postgres.password is required when infra.postgres.enabled and secrets.create" .password -}}
{{- $_ := set $d "POSTGRES_PASSWORD" $pw -}}
{{- $_ := set $d "FOLIO_DATABASE_URL" (printf "postgresql+psycopg://%s:%s@%s-postgres:5432/%s" .user $pw $fn .database) -}}
{{- $_ := set $d "PGVECTOR_DSN" (printf "postgresql://%s:%s@%s-postgres:5432/%s" .user $pw $fn .database) -}}
{{- range .extraDatabases }}
{{- $_ := set $d (include "folio.extraDbKey" .) (required (printf "infra.postgres.extraDatabases %s: password is required with secrets.create" .name) .password) -}}
{{- end }}
{{- end }}{{- end }}
{{- with .Values.infra.rabbitmq }}{{- if .enabled }}
{{- $pw := required "infra.rabbitmq.password is required when infra.rabbitmq.enabled and secrets.create" .password -}}
{{- $_ := set $d "RABBITMQ_PASSWORD" $pw -}}
{{- $_ := set $d "FOLIO_BROKER_URL" (printf "amqp://%s:%s@%s-rabbitmq:5672//" .user $pw $fn) -}}
{{- end }}{{- end }}
{{- with .Values.infra.minio }}{{- if .enabled }}
{{- $pw := required "infra.minio.password is required when infra.minio.enabled and secrets.create" .password -}}
{{- $_ := set $d "MINIO_ROOT_PASSWORD" $pw -}}
{{- $_ := set $d "AWS_ACCESS_KEY_ID" .rootUser -}}
{{- $_ := set $d "AWS_SECRET_ACCESS_KEY" $pw -}}
{{- end }}{{- end }}
{{- $d = mergeOverwrite $d .Values.secrets.values -}}
{{- toYaml $d -}}
{{- end -}}

{{/* non-secret env shared by api/worker/init */}}
{{- define "folio.env" -}}
- {name: FOLIO_CONFIG_DIR, value: /app/project}
- {name: HOME, value: /tmp}
- {name: FOLIO_LOG_LEVEL, value: {{ .Values.config.logLevel | quote }}}
- {name: AWS_REGION, value: {{ .Values.config.awsRegion | quote }}}
{{- $fn := include "folio.fullname" . }}
{{- if .Values.infra.minio.enabled }}
- {name: S3_ENDPOINT_URL, value: "http://{{ $fn }}-minio:9000"}
- {name: FOLIO_STORAGE_URI, value: {{ .Values.config.storageUri | default (printf "s3://%s" .Values.infra.minio.bucket) | quote }}}
{{- else }}
{{- if .Values.external.s3.endpoint }}
- {name: S3_ENDPOINT_URL, value: {{ .Values.external.s3.endpoint | quote }}}
{{- end }}
{{- if .Values.config.storageUri }}
- {name: FOLIO_STORAGE_URI, value: {{ .Values.config.storageUri | quote }}}
{{- end }}
{{- end }}
{{- if .Values.infra.opensearch.enabled }}
- {name: OPENSEARCH_URL, value: "http://{{ $fn }}-opensearch:9200"}
{{- else if .Values.external.opensearch.url }}
- {name: OPENSEARCH_URL, value: {{ .Values.external.opensearch.url | quote }}}
{{- end }}
{{- if .Values.infra.milvus.enabled }}
- {name: MILVUS_URI, value: "http://{{ $fn }}-milvus:19530"}
{{- else if .Values.external.milvus.uri }}
- {name: MILVUS_URI, value: {{ .Values.external.milvus.uri | quote }}}
{{- end }}
{{- if .Values.structure.enabled }}
- {name: FOLIO_STRUCTURE_URL, value: "http://{{ $fn }}-structure:9000"}
{{- else if .Values.external.structure.url }}
- {name: FOLIO_STRUCTURE_URL, value: {{ .Values.external.structure.url | quote }}}
{{- end }}
{{- if .Values.embedStub.enabled }}
- {name: FOLIO_EMBED_URL, value: "http://{{ $fn }}-embed:8001/v1"}
- {name: FOLIO_EMBED_MODEL, value: {{ .Values.embedStub.model | quote }}}
{{- else }}
{{- if .Values.external.embed.url }}
- {name: FOLIO_EMBED_URL, value: {{ .Values.external.embed.url | quote }}}
{{- end }}
{{- if .Values.external.embed.model }}
- {name: FOLIO_EMBED_MODEL, value: {{ .Values.external.embed.model | quote }}}
{{- end }}
{{- end }}
{{- if .Values.vlmStub.enabled }}
- {name: FOLIO_VLM_URL, value: "http://{{ $fn }}-vlm:8002/v1"}
{{- else }}
{{- if .Values.external.vlm.url }}
- {name: FOLIO_VLM_URL, value: {{ .Values.external.vlm.url | quote }}}
{{- end }}
{{- if .Values.external.vlm.model }}
- {name: FOLIO_VLM_MODEL, value: {{ .Values.external.vlm.model | quote }}}
{{- end }}
{{- end }}
{{- if .Values.external.rerank.url }}
- {name: FOLIO_RERANK_URL, value: {{ .Values.external.rerank.url | quote }}}
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* common pod bits for folio app containers: security + project volume mounts */}}
{{- define "folio.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 10001
  capabilities: {drop: [ALL]}
{{- end -}}

{{- define "folio.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{- define "folio.volumeMounts" -}}
- {name: tmp, mountPath: /tmp}
{{- if not .Values.project.fromImage }}
- {name: project, mountPath: /app/project, readOnly: true}
{{- end }}
{{- end -}}

{{- define "folio.volumes" -}}
- {name: tmp, emptyDir: {}}
{{- if not .Values.project.fromImage }}
- name: project
  configMap:
    name: {{ include "folio.fullname" . }}-project
    items:
    {{- range $n, $_ := .Values.project.pipelines }}
      - {key: {{ printf "pipelines__%s" $n | quote }}, path: {{ printf "pipelines/%s" $n | quote }}}
    {{- end }}
    {{- range $n, $_ := .Values.project.schemas.opensearch }}
      - {key: {{ printf "schemas_opensearch__%s" $n | quote }}, path: {{ printf "schemas/opensearch/%s" $n | quote }}}
    {{- end }}
    {{- range $n, $_ := .Values.project.schemas.milvus }}
      - {key: {{ printf "schemas_milvus__%s" $n | quote }}, path: {{ printf "schemas/milvus/%s" $n | quote }}}
    {{- end }}
    {{- if .Values.project.mappers }}
      - {key: mappers.py, path: mappers.py}
    {{- end }}
{{- end }}
{{- end -}}

{{/* Secret key holding an extra database's password; name must be a plain SQL identifier */}}
{{- define "folio.extraDbKey" -}}
{{- if not (regexMatch "^[a-z_][a-z0-9_]*$" .name) }}{{ fail (printf "infra.postgres.extraDatabases: invalid name %q" .name) }}{{ end -}}
{{- if not (regexMatch "^[a-z_][a-z0-9_]*$" .user) }}{{ fail (printf "infra.postgres.extraDatabases: invalid user %q" .user) }}{{ end -}}
POSTGRES_{{ upper .name }}_PASSWORD
{{- end -}}

{{/* dependency wait initContainers for bundled infra (job mode) */}}
{{- define "folio.waitFor" -}}
{{- $fn := include "folio.fullname" . -}}
{{- $targets := list -}}
{{- if .Values.infra.postgres.enabled }}{{ $targets = append $targets (printf "%s-postgres:5432" $fn) }}{{ end -}}
{{- if .Values.infra.rabbitmq.enabled }}{{ $targets = append $targets (printf "%s-rabbitmq:5672" $fn) }}{{ end -}}
{{- if .Values.infra.opensearch.enabled }}{{ $targets = append $targets (printf "%s-opensearch:9200" $fn) }}{{ end -}}
{{- if .Values.infra.minio.enabled }}{{ $targets = append $targets (printf "%s-minio:9000" $fn) }}{{ end -}}
{{- if .Values.infra.milvus.enabled }}{{ $targets = append $targets (printf "%s-milvus:19530" $fn) }}{{ end -}}
{{- if $targets }}
initContainers:
  - name: wait-for-infra
    image: {{ include "folio.appImage" . }}
    imagePullPolicy: {{ .Values.image.pullPolicy }}
    command: ["python", "-c"]
    args:
      - |
        import socket, sys, time
        for t in sys.argv[1:]:
            h, p = t.rsplit(":", 1)
            for _ in range(180):
                try:
                    socket.create_connection((h, int(p)), 2).close(); break
                except OSError:
                    time.sleep(2)
            else:
                sys.exit("timeout waiting for " + t)
      {{- range $targets }}
      - {{ . | quote }}
      {{- end }}
    {{- include "folio.containerSecurity" . | nindent 4 }}
{{- with .Values.infra.postgres }}{{- if and .enabled .extraDatabases }}
  # Extra databases + owners on the bundled server: re-running is safe (create if missing, reset password).
  - name: create-databases
    image: {{ include "folio.image" (dict "root" $ "repository" .image.repository "tag" .image.tag) }}
    imagePullPolicy: {{ $.Values.image.pullPolicy }}
    command: ["sh", "-c"]
    args:
      - |
        set -e
        {{- range .extraDatabases }}
        psql -X -v ON_ERROR_STOP=1 -v db={{ .name }} -v usr={{ .user }} -v pw="$POSTGRES_{{ upper .name }}_PASSWORD" <<'SQL'
        SELECT format('CREATE ROLE %I LOGIN', :'usr') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'usr')\gexec
        ALTER ROLE :"usr" WITH LOGIN PASSWORD :'pw';
        SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'usr') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db')\gexec
        SQL
        echo "database {{ .name }} ready"
        {{- end }}
    env:
      - {name: PGHOST, value: {{ printf "%s-postgres" (include "folio.fullname" $) | quote }}}
      - {name: PGUSER, value: {{ .user | quote }}}
      - {name: PGDATABASE, value: {{ .database | quote }}}
      - name: PGPASSWORD
        valueFrom: {secretKeyRef: {name: {{ include "folio.secretName" $ }}, key: POSTGRES_PASSWORD}}
      {{- range .extraDatabases }}
      - name: {{ include "folio.extraDbKey" . }}
        valueFrom: {secretKeyRef: {name: {{ include "folio.secretName" $ }}, key: {{ include "folio.extraDbKey" . }}}}
      {{- end }}
    {{- include "folio.containerSecurity" $ | nindent 4 }}
{{- end }}{{- end }}
{{- end }}
{{- end -}}
