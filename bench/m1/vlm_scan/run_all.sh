#!/bin/bash
# Whole vlm_scan bench under the GPU lock: start m1-vlm (Qwen3.5-9B), run the three policies, stop m1-vlm.
#   flock ~/folio-m1-bench/gpu.lock bench/m1/vlm_scan/run_all.sh   (run from ~/folio-m1-bench/folio-scan)
set -u
B=~/folio-m1-bench; O=$B/out/vlm_scan; mkdir -p $O
PY=$B/venv-paddle/bin/python
GMU=0.3 bash bench/m1/vlm/serve.sh Qwen3.5-9B > $O/serve.id
for i in $(seq 120); do curl -sf localhost:8010/v1/models >/dev/null && break; sleep 10; done
curl -sf localhost:8010/v1/models >/dev/null || { docker logs --tail 50 m1-vlm > $O/serve.log 2>&1; docker rm -f m1-vlm; exit 1; }
for P in ${POLICIES:-off ocr_first page}; do
  /usr/bin/time -v nice $PY bench/m1/vlm_scan/run.py --policy $P --out $O/$P.json > $O/$P.log 2>&1
done
docker logs --tail 200 m1-vlm > $O/serve.log 2>&1
docker rm -f m1-vlm
touch $O/done.flag
