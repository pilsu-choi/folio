#!/bin/bash
# Download DOCX samples (samples.txt: name|url) and export reference PDFs with MS Word when run from WSL.
# Fonts come from ../hwpx-converters/fetch.sh (shared fonts/ directory).
set -eu
B=$(cd "$(dirname "$0")"; pwd); cd "$B"; mkdir -p samples ref
while IFS='|' read -r name url member; do
  [ -z "$name" ] || [ "${name:0:1}" = "#" ] && continue
  [ -s "samples/$name.docx" ] && continue
  if [ -z "$member" ]; then curl -fsSL -o "samples/$name.docx" "$url"
  else  # docx inside a zip (cp949 member names)
    curl -fsSL -o "samples/$name.zip" "$url"
    python3 -I -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); m={i.filename.encode("cp437").decode("cp949") if not i.flag_bits & 0x800 else i.filename: i for i in z.infolist()}; open(sys.argv[3],"wb").write(z.read(m[sys.argv[2]]))' "samples/$name.zip" "$member" "samples/$name.docx"
    rm "samples/$name.zip"
  fi; sleep 2
done < samples.txt
command -v powershell.exe >/dev/null || { echo "no powershell.exe: export ref/*.pdf with word_export.ps1 on Windows"; exit 0; }
W=$(powershell.exe -NoProfile -Command '[IO.Path]::GetTempPath()' | tr -d '\r')docx-bench; L=$(wslpath "$W")
rm -rf "$L"; mkdir -p "$L/in" "$L/ref"; cp samples/*.docx "$L/in/"; cp word_export.ps1 "$L/"
: > ref/word_export.log
for f in "$L"/in/*.docx; do  # one Word process per file, so a document that hangs Word only fails itself
  timeout ${WORD_TIMEOUT:-180} powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$W\\word_export.ps1" "$W\\in\\$(basename "$f")" "$W\\ref" | tr -d '\r' | tee -a ref/word_export.log
  [ "${PIPESTATUS[0]}" = 124 ] && echo "TIMEOUT $(basename "$f" .docx)" | tee -a ref/word_export.log
  # a hidden Word that hung survives the timeout: stop only automation instances
  powershell.exe -NoProfile -Command 'Get-WmiObject Win32_Process | ? { $_.Name -eq "WINWORD.EXE" -and $_.CommandLine -match "/Automation" } | % { Stop-Process -Id $_.ProcessId -Force }'
done
cp "$L"/ref/*.pdf ref/; rm -rf "$L"
