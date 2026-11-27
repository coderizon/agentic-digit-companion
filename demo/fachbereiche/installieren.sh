#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo 'Aufruf: bash installieren.sh /pfad/zur/admin-anmeldung.json' >&2
  exit 2
fi
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export FREIBURG_ROOT="${FREIBURG_ROOT:-$HOME/.local/share/agentic-rag-digitom}"
runtime=$(cd -- "$FREIBURG_ROOT" && pwd -P)
export FREIBURG_ROOT="$runtime"
if [[ -f "$runtime/run/settings.sh" ]]; then
  source "$runtime/run/settings.sh"
fi
export PATH="$runtime/tools:$PATH"
target="$runtime/demo/fachbereiche"
mkdir -p "$target"
if [[ "$here" != "$target" ]]; then
  for file in fall.py einrichten.py chat_pipe.py werkzeuge.py excel_export.py pruefvermerk.xlsx abnahme.py excel_abnahme.py zugang.py it_service.py kix_client.py it_einrichten.py kix_einrichten.py kix_abnahme.py freibot_ticket.py sprachfrage.py "IT-Handbuch RaumPlan.md"; do
    install -m 0644 "$here/$file" "$target/$file"
  done
fi
uv venv --allow-existing --python "$runtime/.webui/bin/python" "$runtime/run/fachbereiche-mcp"
uv pip install --python "$runtime/run/fachbereiche-mcp/bin/python" 'mcp==1.26.0' 'httpx>=0.28,<1' 'openpyxl==3.1.5'
"$runtime/.venv/bin/python" "$target/einrichten.py" --port "${FREIBURG_WEBUI_PORT:-8077}" --admin-file "$1"
"$runtime/.venv/bin/python" "$target/abnahme.py"
"$runtime/.webui/bin/python" "$target/excel_abnahme.py"
