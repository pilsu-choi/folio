#!/bin/bash
# usage: run_model.sh <model-dir> "<extra vllm args>"   (holds the GPU lock for the whole serve+eval+teardown)
M=$1; EXTRA=$2; O=~/folio-m1-bench/out/vlm
mkdir -p $O/$M
flock ~/folio-m1-bench/gpu.lock bash -c "
  LC_ALL=C free -m | awk '/Mem/{print \"mem_used_before_MB\",\$3}' > $O/$M/mem.txt
  bash ~/folio-m1-bench/vlm_serve.sh $M >/dev/null
  until curl -sf localhost:8010/v1/models >/dev/null; do sleep 5; done
  LC_ALL=C free -m | awk '/Mem/{print \"mem_used_loaded_MB\",\$3}' >> $O/$M/mem.txt
  python3 ~/folio-m1-bench/vlm_bench.py $M $O > $O/$M/run.log 2>&1
  LC_ALL=C free -m | awk '/Mem/{print \"mem_used_after_run_MB\",\$3}' >> $O/$M/mem.txt
  docker logs m1-vlm 2>&1 | grep -E 'Model loading took|Available KV cache|GPU KV cache size|Actual usage' > $O/$M/vllm_mem.txt
  docker logs m1-vlm > $O/$M/vllm.log 2>&1
  docker rm -f m1-vlm
"
