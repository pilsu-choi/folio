# syntax=docker/dockerfile:1
# folio: API / worker / CLI shared image. Role is chosen by the command (ENTRYPOINT is `folio`).
# Build from the repo root:  docker build -f deploy/docker/Dockerfile -t folio:0.1.0 .
# No secrets are baked in; all connection info comes from environment variables.

FROM python:3.12-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY folio ./folio
COPY service ./service
# Build wheels for folio and all of its runtime dependencies (extras: opensearch, milvus, pgvector, s3, service).
RUN pip wheel --wheel-dir /wheels ".[all]"

FROM python:3.12-slim AS runtime
ARG FOLIO_VERSION=0.1.0
LABEL org.opencontainers.image.title="folio" \
      org.opencontainers.image.description="Document to search-index pipeline: API, worker and CLI" \
      org.opencontainers.image.version="${FOLIO_VERSION}" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.source="https://example.invalid/folio"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FOLIO_CONFIG_DIR=/app/project HOME=/home/folio
RUN groupadd --gid 10001 folio \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/folio --shell /usr/sbin/nologin folio
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels "folio[all]==${FOLIO_VERSION}"
# Default project (customer images replace it via deploy/docker/Dockerfile.project).
COPY --chown=10001:10001 project-template/ /app/project/
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"]
ENTRYPOINT ["folio"]
CMD ["api"]
