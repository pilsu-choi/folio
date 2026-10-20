#!/bin/bash
# Re-run the disagreeing samples with --save to inspect outputs (under the GPU lock).
set -u
B=~/folio-m1-bench; O=$B/out/vlm_scan/debug; mkdir -p $O; PY=$B/venv-paddle/bin/python
GMU=0.3 bash bench/m1/vlm/serve.sh Qwen3.5-9B > /dev/null
for i in $(seq 120); do curl -sf localhost:8010/v1/models >/dev/null && break; sleep 10; done
nice $PY bench/m1/vlm_scan/run.py --policy page --ids s02_fsc_audit_p2,s07_fsc_virtual_asset_p2,s05_terms_bankloan_p3 --save $O --out $O/page.json > $O/page.log 2>&1
nice $PY bench/m1/vlm_scan/run.py --policy ocr_first --ids s03_fsc_token_rule_p3,s09_kostat_elderly_p6,t04_application_form --save $O --out $O/ocr_first.json > $O/ocr_first.log 2>&1
docker rm -f m1-vlm >/dev/null; touch $O/done.flag
