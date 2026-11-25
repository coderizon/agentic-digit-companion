#!/usr/bin/env bash
# The Vast container cannot start Docker. This installs the same UI release
# in its own environment, keeping its Transformers dependencies off the RAG path.
set -euo pipefail
R="${FREIBURG_ROOT:-/srv/freiburg-openrag}"
if [[ ! -x "$R/.webui/bin/python" ]]; then uv venv --python "${FREIBURG_PYTHON:-$(command -v python3)}" "$R/.webui"; fi
uv pip install --python "$R/.webui/bin/python" --torch-backend cpu -r "$R/ops/requirements-webui.lock"
printf '%s\n' 'Start with FREIBURG_WEBUI_NATIVE=1 bash ops/webui.sh'
