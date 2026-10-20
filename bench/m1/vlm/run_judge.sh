#!/bin/bash
# Serve the judge (gemma-4-12B-it, text-only use) under the GPU lock, score figure descriptions, tear down.
O=~/folio-m1-bench/out/vlm
flock ~/folio-m1-bench/gpu.lock bash -c "
  bash ~/folio-m1-bench/vlm_serve.sh gemma-4-12B-it >/dev/null
  until curl -sf localhost:8010/v1/models >/dev/null; do sleep 5; done
  python3 ~/folio-m1-bench/vlm_judge.py $O gemma-4-12B-it > $O/judge.log 2>&1
  docker logs m1-vlm > $O/judge_vllm.log 2>&1
  docker rm -f m1-vlm
"
