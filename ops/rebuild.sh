#!/usr/bin/env bash
# Capture public sources and activate only a complete, newly built generation.
set -euo pipefail
export FREIBURG_ROOT="${FREIBURG_ROOT:-/srv/freiburg-openrag}"
R="$FREIBURG_ROOT"
export PYTHONPATH="$R/app:$R/vendor" TOKENIZERS_PARALLELISM=false
export FREIBURG_VISION_DEVICE="${FREIBURG_VISION_BUILD_DEVICE:-cuda:0}"
export FREIBURG_SEARCH_DEVICE="${FREIBURG_SEARCH_DEVICE:-cuda:0}"
mkdir -p "$R/data"
exec 9>"$R/data/.import.lock"
flock -n 9
tesseract --list-langs | grep -qx deu
generation="$("$R/.venv/bin/python" - <<'PY'
from import_sources import Capture
c = Capture()
c.law()
c.portal()
c.papers()
c.mobility()
c.publish()
print(c.path)
PY
)"
gen="$(tail -n 1 <<< "$generation")"
[[ "$(dirname "$gen")" == "$R/data" && "$(basename "$gen")" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
"$R/.venv/bin/python" "$R/app/statistik.py" "$gen"
"$R/.venv/bin/python" "$R/app/topk_city.py" build "$gen"
"$R/.venv/bin/python" "$R/app/visual_city.py" "$gen"
"$R/.venv/bin/python" - "$gen" <<'PY'
import hashlib,json,os,sys,tempfile
from pathlib import Path
p=Path(sys.argv[1])
plan=json.loads((p/'textindex/plan.json').read_bytes())
assert plan['pages']['records'] and plan['corpus_sha256']==hashlib.sha256((p/'pages.jsonl').read_bytes()).hexdigest()
assert (p/'fritz.sqlite').stat().st_size and (p/'bildindex/beschriftungen.json').is_file()
with tempfile.TemporaryDirectory(dir=p.parent) as folder:
    switch=Path(folder)/'next'
    switch.symlink_to(p.name)
    os.replace(switch,p.parent/'import')
print('Activated',p.name)
PY
