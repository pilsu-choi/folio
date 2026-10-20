#!/bin/bash
# Download candidate + judge models into ~/folio-m1-bench/models (via vllm image's huggingface_hub). Logs sizes.
for m in PaddlePaddle/PaddleOCR-VL-1.6 zai-org/GLM-OCR Qwen/Qwen3.5-9B Qwen/Qwen3-VL-8B-Instruct google/gemma-4-12B-it; do
  docker run --rm --name m1-dl -u $(id -u):$(id -g) -e HOME=/tmp -v ~/folio-m1-bench/models:/models --entrypoint python3 vllm/vllm-openai:v0.31.0 \
    -c "from huggingface_hub import snapshot_download as s; s('$m', local_dir='/models/${m#*/}', allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.py','*.jinja','*.tiktoken'])"
  du -sh ~/folio-m1-bench/models/${m#*/} | tee -a ~/folio-m1-bench/out/vlm/downloads.log
done
echo DONE >> ~/folio-m1-bench/out/vlm/downloads.log
