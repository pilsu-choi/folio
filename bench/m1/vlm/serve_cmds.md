Per-model extra args (appended to serve.sh):
- PaddleOCR-VL-1.6: --no-enable-prefix-caching --mm-processor-cache-gb 0
- GLM-OCR:          (none)
- Qwen3.5-9B:       --default-chat-template-kwargs '{"enable_thinking": false}' --limit-mm-per-prompt '{"video":0}'
- Qwen3-VL-8B-Instruct: --limit-mm-per-prompt '{"video":0}'
- gemma-4-12B-it (judge, text only): --max-model-len 8192
All GPU use: flock ~/folio-m1-bench/gpu.lock <cmd>
