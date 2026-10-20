#!/bin/bash
# usage: serve.sh <model-dir-name>  -> container m1-vlm on :8010 (stop: docker rm -f m1-vlm). Model-specific vLLM args below.
M=$1
case $M in
  PaddleOCR-VL-1.6) X=(--no-enable-prefix-caching --mm-processor-cache-gb 0);;
  Qwen3.5-9B) X=(--default-chat-template-kwargs '{"enable_thinking": false}' --limit-mm-per-prompt '{"video":0}');;
  Qwen3-VL-8B-Instruct) X=(--limit-mm-per-prompt '{"video":0}');;
  gemma-4-12B-it) X=(--limit-mm-per-prompt '{"image":0,"audio":0}');;
  *) X=();;
esac
docker rm -f m1-vlm >/dev/null 2>&1
docker run -d --name m1-vlm --gpus all --ipc host -p 8010:8000 -v ~/folio-m1-bench/models:/models:ro \
  vllm/vllm-openai:v0.31.0 --model /models/$M --served-model-name $M --trust-remote-code \
  --gpu-memory-utilization ${GMU:-0.25} --max-model-len ${MAXLEN:-16384} "${X[@]}"
