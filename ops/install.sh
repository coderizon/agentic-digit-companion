#!/usr/bin/env bash
# Lower-level installer for retrieval and inference. setup.sh also provisions Open WebUI.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
export FREIBURG_ROOT="${FREIBURG_ROOT:-/srv/freiburg-openrag}"
archive="${1:?Pass the city snapshot .tar.gz, --existing or --fresh}"
expected="${2:-4c41191019044dd248de7203f040ab850434382ea175d4c3b28ee63784e12534}"
python="${FREIBURG_PYTHON:-$(command -v python3)}"
for tool in uv curl sha256sum nvidia-smi tar; do command -v "$tool" >/dev/null; done
"$python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
mkdir -p "$FREIBURG_ROOT"/{app,ops,vendor,eval,demo,logs,results,models,assets}
if [[ "$(realpath "$repo")" != "$(realpath "$FREIBURG_ROOT")" ]]; then
    for folder in app vendor ops eval demo; do
        (cd "$repo" && tar --exclude='*.key' --exclude=__pycache__ -cf - "$folder") | tar -xf - -C "$FREIBURG_ROOT"
    done
    cp "$repo/README.md" "$repo/AGENTS.md" "$repo/BETRIEB.md" "$repo/BENCHMARKS.md" "$FREIBURG_ROOT/"
fi
if [[ "$archive" == --existing ]]; then
    for file in sources.json fritz.sqlite textindex/plan.json bildindex/katalog.json; do
        test -s "$FREIBURG_ROOT/data/import/$file" || { echo "Datenstand unvollständig: $file" >&2; exit 1; }
    done
elif [[ "$archive" != --fresh ]]; then
    "$python" "$FREIBURG_ROOT/ops/import_snapshot.py" "$archive" --sha256 "$expected"
fi
"$python" - <<'PY'
import os, secrets
from pathlib import Path
root = Path(os.environ["FREIBURG_ROOT"]) / "app"
for name in ("chat","reader","topk","ui"):
    path = root / (name+".key")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o600)
    except FileExistsError:
        if len(path.read_text().strip()) < 32 or path.stat().st_mode & 0o077:
            raise SystemExit(f"Check existing service key permissions/length: {path}")
    else:
        with os.fdopen(fd,"w") as out:
            out.write(secrets.token_urlsafe(36)+"\n")
PY
venv="$FREIBURG_ROOT/.venv"
if [[ ! -x "$venv/bin/python" ]]; then uv venv --python "$python" "$venv"; fi
uv pip install --python "$venv/bin/python" --torch-backend cu126 -r "$FREIBURG_ROOT/ops/requirements.lock"
export STRATA_FEED="https://github.com/Niko1221/Strata/releases/download/v0.2.21"
installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
curl --fail --location --retry 3 "$STRATA_FEED/install.sh" --output "$installer"
printf '%s  %s\n' d67f9fb21904dad2f19909452e67b79ba7f0fc93454b9447c3a02b91fc0002e4 "$installer" | sha256sum -c -
sh "$installer"
"$venv/bin/python" "$FREIBURG_ROOT/ops/models.py"
bash "$FREIBURG_ROOT/ops/fetch-model.sh"
if [[ "$archive" == --fresh ]]; then bash "$FREIBURG_ROOT/ops/rebuild.sh"; fi
echo "Installation complete. Start search, reader and gateway with ops/run.sh."
