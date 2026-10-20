#!/bin/bash
cd ~/folio-m1-bench; O=out/backends
flock gpu.lock /usr/bin/time -v venv-docling/bin/python $O/run_manifest.py --backend docling --device cuda --out $O/docling_cuda.json > $O/docling_cuda.log 2>&1
/usr/bin/time -v venv-docling/bin/python $O/run_manifest.py --backend docling --device cpu --out $O/docling_cpu.json > $O/docling_cpu.log 2>&1
/usr/bin/time -v venv-paddle/bin/python $O/run_manifest.py --backend ppstructure --device cpu --out $O/ppstructure_cpu.json > $O/ppstructure_cpu.log 2>&1
echo ALLDONE > $O/done.flag
