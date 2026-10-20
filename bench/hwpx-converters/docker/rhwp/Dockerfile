FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl fontconfig poppler-utils && rm -rf /var/lib/apt/lists/*
ARG V=v0.8.7
RUN set -e; cd /tmp; \
 curl -fsSLO https://github.com/edwardkim/rhwp/releases/download/$V/rhwp-$V-linux-$(uname -m).tar.gz; \
 curl -fsSLO https://github.com/edwardkim/rhwp/releases/download/$V/SHA256SUMS.txt; \
 grep "linux-$(uname -m)" SHA256SUMS.txt | sha256sum -c -; \
 tar xzf rhwp-$V-linux-$(uname -m).tar.gz; install -m755 rhwp/rhwp /usr/local/bin/rhwp; rm -rf /tmp/*
ENTRYPOINT []
