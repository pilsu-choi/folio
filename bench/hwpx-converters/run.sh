#!/bin/bash
# Sequential HWPX converter benchmark (one tool at a time). usage: bash run.sh (after fetch.sh)
set -u
B=$(cd "$(dirname "$0")"; pwd); cd "$B"
LOOP='for r in 1 2; do for f in /in/*.hwpx; do n=$(basename $f .hwpx); s=$(date +%s%N); eval "$(echo "$CMD" | sed "s#{in}#$f#g; s#{n}#$n#g")" >/out/$n.log 2>&1; rc=$?; e=$(date +%s%N); echo "$r $n $rc $(( (e - s) / 1000000 ))"; done; done'
V="-v $B/fonts:/usr/share/fonts/bench:ro -v $B/samples:/in:ro"
for t in libreoffice rhwp hwpforge kordoc; do docker build -q -t hwpx-bench-$t docker/$t >/dev/null || echo "BUILD FAIL $t"; done
docker image ls --format '{{.Repository}} {{.Size}}' | grep hwpx-bench > images.txt
run() { t=$1; shift; rm -rf out/$t; mkdir -p out/$t
  echo "== $t load $(cut -d' ' -f1 /proc/loadavg)"
  docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp $V -v $B/out/$t:/out --entrypoint bash -e CMD="$1" -e PRE="${2:-true}" hwpx-bench-$t -c "eval \"\$PRE\"; $LOOP" > out/$t/timing.txt
}
run libreoffice 'timeout 300 soffice -env:UserInstallation=file:///tmp/p --headless --convert-to pdf:writer_pdf_Export --outdir /out {in}' \
  'soffice -env:UserInstallation=file:///tmp/p --headless --convert-to pdf --outdir /tmp/w /in/kftc_press.hwpx >/dev/null 2>&1'
run rhwp 'timeout 300 rhwp export-pdf {in} -o /out/{n}.pdf --font-path /usr/share/fonts/bench'
run kordoc 'timeout 300 kordoc --silent {in} -o /out/{n}.md && timeout 300 kordoc --silent --no-images --format json {in} -o /out/{n}.json' 'export KORDOC_OFFLINE=1'
# hwpforge: in-process script (strict, then degraded)
mkdir -p out/hwpforge; docker run --rm --user $(id -u):$(id -g) -v $B/fonts:/fonts:ro -v $B/samples:/in:ro -v $B/out/hwpforge:/out hwpx-bench-hwpforge > out/hwpforge/run.log 2>&1
python3 - <<'PY'
import json,collections
for t in ["libreoffice","rhwp","kordoc"]:
    d=collections.defaultdict(list); rc={}
    for l in open(f"out/{t}/timing.txt"):
        r,n,c,ms=l.split(); d[n].append(int(ms)/1000); rc[n]=int(c)
    json.dump([{"name":n,"ok":rc[n]==0,"seconds":min(v),"runs":v} for n,v in sorted(d.items())],open(f"out/{t}/results.json","w"),indent=1)
print("done")
PY
echo "load end $(cut -d' ' -f1 /proc/loadavg)"
