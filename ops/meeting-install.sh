#!/usr/bin/env bash
# Installs the optional German speech recognizer alongside the existing RAG.
set -euo pipefail
R="${FREIBURG_ROOT:-$(cd "$(dirname "$0")/.." && pwd -P)}"
uv venv --allow-existing --python "$R/.venv/bin/python" "$R/.audio"
uv pip install --python "$R/.audio/bin/python" 'faster-whisper==1.2.1' 'av==15.1.0'
"$R/.audio/bin/python" - "$R/models/meeting-whisper-small" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download("Systran/faster-whisper-small", revision="536b0662742c02347bc0e980a01041f333bce120",
                  local_dir=sys.argv[1], allow_patterns=["*.json", "*.txt", "model.bin"])
PY
