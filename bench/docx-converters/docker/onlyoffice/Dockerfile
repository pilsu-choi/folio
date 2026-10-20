FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils fontconfig poppler-utils && rm -rf /var/lib/apt/lists/*
ARG V=v9.4.0
RUN curl -fsSL https://github.com/ONLYOFFICE/DocumentBuilder/releases/download/$V/onlyoffice-documentbuilder-linux-$(uname -m).tar.xz | tar xJ -C / \
 && ln -s /opt/onlyoffice/documentbuilder/docbuilder /usr/local/bin/docbuilder
ENTRYPOINT []
