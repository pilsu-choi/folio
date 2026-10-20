FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-java-common default-jre-headless \
      ca-certificates curl unzip fontconfig poppler-utils python3 \
 && rm -rf /var/lib/apt/lists/*
ARG H2O_URL=https://github.com/ebandal/H2Orestart/releases/download/v0.8.0/H2Orestart.oxt
RUN curl -fsSL -o /tmp/H2Orestart.oxt "$H2O_URL" \
 && unopkg add --shared /tmp/H2Orestart.oxt \
 && unopkg list --shared | grep -i -A3 h2o ; rm -f /tmp/H2Orestart.oxt
RUN unopkg list --shared | grep -qi h2orestart
