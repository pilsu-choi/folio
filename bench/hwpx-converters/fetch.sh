#!/bin/bash
# Download benchmark inputs: HWPX samples + Hancom-exported reference PDFs (rhwp repo) and open Korean fonts.
set -eu
B=$(cd "$(dirname "$0")"; pwd); cd "$B"; mkdir -p samples ref fonts
RAW=https://raw.githubusercontent.com/edwardkim/rhwp/1a76570e833917d15817415a53c09ad61ab3203f
while IFS='|' read -r name src ref; do
  curl -fsSL -o "samples/$name.hwpx" "$RAW/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$src")"
  curl -fsSL -o "ref/$name.pdf" "$RAW/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$ref")"
done < samples.txt
docker run --rm -v "$B/fonts:/out" ubuntu:24.04 bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fonts-noto-cjk fonts-noto-cjk-extra fonts-nanum fonts-nanum-extra fonts-unfonts-core >/dev/null && cp -r /usr/share/fonts/opentype/noto /usr/share/fonts/truetype/nanum /usr/share/fonts/truetype/unfonts-core /out/ && chown -R $(id -u):$(id -g) /out"
