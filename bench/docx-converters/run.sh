#!/bin/bash
# Sequential DOCX converter benchmark (one tool at a time). usage: bash run.sh (after fetch.sh)
# Reuses the HWPX bench images for LibreOffice and kordoc, and its fonts/ directory.
# env: FONTS (font dir), OUT (output dir), TOOLS (subset), CPUS, MEM
set -u
B=$(cd "$(dirname "$0")"; pwd); cd "$B"; H=$B/../hwpx-converters
LOOP='for r in 1 2; do for f in /in/*.docx; do n=$(basename $f .docx); s=$(date +%s%N); eval "$(echo "$CMD" | sed "s#{in}#$f#g; s#{n}#$n#g")" >/out/$n.log 2>&1; rc=$?; e=$(date +%s%N); echo "$r $n $rc $(( (e - s) / 1000000 ))"; done; done'
FONTS=${FONTS:-$H/fonts}; OUT=${OUT:-out}; TOOLS=${TOOLS:-libreoffice onlyoffice kordoc}
V="-v $FONTS:/usr/share/fonts/bench:ro -v $B/samples:/in:ro"
LIMIT="--cpus ${CPUS:-4} --memory ${MEM:-6g}"
docker build -q -t docx-bench-libreoffice $H/docker/libreoffice >/dev/null || echo "BUILD FAIL libreoffice"
docker build -q -t docx-bench-kordoc $H/docker/kordoc >/dev/null || echo "BUILD FAIL kordoc"
docker build -q -t docx-bench-onlyoffice docker/onlyoffice >/dev/null || echo "BUILD FAIL onlyoffice"
docker image ls --format '{{.Repository}} {{.Size}}' | grep docx-bench > images.txt
run() { t=$1; shift; [[ " $TOOLS " == *" $t "* ]] || return 0; rm -rf $OUT/$t; mkdir -p $OUT/$t
  echo "== $t load $(cut -d' ' -f1 /proc/loadavg)"
  docker run --rm $LIMIT --user $(id -u):$(id -g) -e HOME=/tmp $V -v $B/$OUT/$t:/out --entrypoint bash -e CMD="$1" -e PRE="${2:-true}" docx-bench-$t -c "eval \"\$PRE\"; $LOOP" > $OUT/$t/timing.txt
}
run libreoffice 'timeout 300 soffice -env:UserInstallation=file:///tmp/p --headless --convert-to pdf:writer_pdf_Export --outdir /out {in}' \
  'f=$(ls /in/*.docx | head -1); soffice -env:UserInstallation=file:///tmp/p --headless --convert-to pdf --outdir /tmp/w $f >/dev/null 2>&1'
run onlyoffice 'printf "builder.OpenFile(\"%s\");\nbuilder.SaveFile(\"pdf\", \"%s\");\nbuilder.CloseFile();\n" {in} /out/{n}.pdf > /tmp/c.docbuilder && timeout 300 docbuilder /tmp/c.docbuilder'
run kordoc 'timeout 300 kordoc --silent {in} -o /out/{n}.md && timeout 300 kordoc --silent --no-images --format json {in} -o /out/{n}.json' 'export KORDOC_OFFLINE=1'
# fonts actually embedded by each PDF tool (fallback check)
for t in libreoffice onlyoffice; do [[ " $TOOLS " == *" $t "* ]] || continue
  docker run --rm --user $(id -u):$(id -g) -v $B/$OUT/$t:/out docx-bench-$t bash -c 'for f in /out/*.pdf; do echo "## $(basename $f)"; pdffonts $f | tail -n +3 | cut -c1-40 | sort -u; done' > $OUT/$t/fonts.txt
done
OUT=$OUT TOOLS=$TOOLS python3 - <<'PY'
import json,collections,os
for t in os.environ["TOOLS"].split():
    out=os.environ["OUT"]; d=collections.defaultdict(list); rc={}
    for l in open(f"{out}/{t}/timing.txt"):
        r,n,c,ms=l.split(); d[n].append(int(ms)/1000); rc[n]=int(c)
    json.dump([{"name":n,"ok":rc[n]==0,"seconds":min(v),"runs":v} for n,v in sorted(d.items())],open(f"{out}/{t}/results.json","w"),indent=1)
print("done")
PY
echo "load end $(cut -d' ' -f1 /proc/loadavg)"
