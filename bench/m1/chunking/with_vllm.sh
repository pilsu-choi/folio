#!/bin/bash
# usage: with_vllm.sh llm|embed <command...>   -- starts an m1-chunk-* vLLM container, runs the command, always removes the container.
# Call under: flock ~/folio-m1-bench/gpu.lock ...
KIND=$1; shift
if [ "$KIND" = llm ]; then
  NAME=m1-chunk-llm; PORT=8021
  ARGS=(--model /models/Qwen3.8-27B-NVFP4 --served-model-name qwen --gpu-memory-utilization ${GMU:-0.30} --max-model-len 16384 \
        --limit-mm-per-prompt '{"image":0,"video":0}' --default-chat-template-kwargs '{"enable_thinking": false}')
else
  NAME=m1-chunk-embed; PORT=8022
  ARGS=(--model /models/bge-m3 --served-model-name bge-m3 --runner pooling --max-model-len 8192 --gpu-memory-utilization ${GMU:-0.08})
fi
trap 'docker rm -f $NAME >/dev/null 2>&1' EXIT
docker rm -f $NAME >/dev/null 2>&1
docker run -d --name $NAME --gpus all --ipc host -p $PORT:8000 -v /mnt/ssd/models:/models:ro vllm/vllm-openai:v0.31.0 "${ARGS[@]}" >/dev/null || exit 1
for i in $(seq 1 180); do
  curl -sf localhost:$PORT/v1/models >/dev/null && break
  docker ps -q -f name=$NAME | grep -q . || { docker logs $NAME 2>&1 | tail -30; exit 2; }
  sleep 10
done
curl -sf localhost:$PORT/v1/models >/dev/null || { docker logs $NAME 2>&1 | tail -30; exit 3; }
echo "$NAME ready on $PORT"
"$@"
