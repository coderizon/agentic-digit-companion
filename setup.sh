#!/usr/bin/env bash
# Install the DIGITOM workspace on one Linux machine with four assigned T4s.
set -euo pipefail
umask 077
repo=$(cd "$(dirname "$0")" && pwd -P)
usage() {
  cat <<'HELP'
bash setup.sh [--check] [--snapshot FILE | --fresh] [--no-systemd]
              [--admin-file FILE | --admin-token-file FILE]
  --check        Nur Hardware, Speicherort, Datenquelle und Ports prüfen.
  --snapshot     Geprüften Freiburger Datenstand vom 24.11.2026 importieren.
  --fresh        Öffentliche Originale neu abrufen und Indizes erstellen.
  --no-systemd   Dienste mit dem lokalen Supervisor im Hintergrund starten.
  --admin-file   Private JSON-Datei mit email und password (Modus 600).
  --admin-token-file  Privates Open-WebUI-Admin-Sitzungstoken (Modus 600).
Ohne Anmeldedatei fragt die Einrichtung im Terminal nach dem WebUI-Konto.
Zielpfad: FREIBURG_ROOT oder ~/.local/share/agentic-rag-digitom.
HELP
}
fail() { printf '%s\n' "$*" >&2; exit 1; }
check=0; no_systemd=0; source_mode=""; credentials=()
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --check) check=1 ;;
    --no-systemd) no_systemd=1 ;;
    --fresh|--snapshot)
      [[ -z "$source_mode" ]] || fail "Nur eine Datenquelle wählen."
      source_mode=$1
      if [[ $1 == --snapshot ]]; then
        [[ -n ${2:-} ]] || fail "--snapshot benötigt einen Dateipfad."
        source_mode=$(realpath -m -- "$2"); shift
      fi ;;
    --admin-file|--admin-token-file)
      [[ ${#credentials[@]} == 0 && -r ${2:-} ]] || fail "Genau eine lesbare Anmeldedatei angeben."
      credentials=("$1" "$(realpath -- "$2")"); shift ;;
    *) usage >&2; fail "Unbekannter Parameter: $1" ;;
  esac
  shift
done
R=$(realpath -m -- "${FREIBURG_ROOT:-$HOME/.local/share/agentic-rag-digitom}")
[[ $R != / && $R != *$'\n'* && $R != *$'\r'* ]] || fail "Ein eigenes Installationsverzeichnis angeben."
export FREIBURG_ROOT="$R"
settings=(STRATA_BIN_DIR STRATA_BIN STRATA_DATA_DIR STRATA_ASSETS FREIBURG_WEBUI_PORT FREIBURG_GATEWAY_PORT FREIBURG_SEARCH_PORT FREIBURG_READER_PORT FREIBURG_READER_GPUS FREIBURG_SEARCH_GPU FREIBURG_RERANK_GPU FREIBURG_SEARCH_DEVICE FREIBURG_RERANK_DEVICE FREIBURG_VISION_DEVICE FREIBURG_CONTEXT FREIBURG_MODEL_SLOTS FREIBURG_DRAFTING OMP_NUM_THREADS FREIBURG_BLAS_THREADS FREIBURG_WEBUI_DATA)
declare -A supplied=()
for key in "${settings[@]}"; do
  if [[ -v $key ]]; then supplied[$key]=${!key}; fi
done
previous_ports=""
if [[ -f "$R/run/settings.sh" ]]; then
  source "$R/run/settings.sh"
  previous_ports="$FREIBURG_WEBUI_PORT,$FREIBURG_GATEWAY_PORT,$FREIBURG_SEARCH_PORT,$FREIBURG_READER_PORT"
fi
for key in "${!supplied[@]}"; do export "$key=${supplied[$key]}"; done
export STRATA_BIN_DIR="${STRATA_BIN_DIR:-$R/tools}" STRATA_DATA_DIR="${STRATA_DATA_DIR:-$R/strata-runtime}" STRATA_ASSETS="${STRATA_ASSETS:-$R/assets}"
export STRATA_BIN="${STRATA_BIN:-$STRATA_BIN_DIR/strata}"
export FREIBURG_WEBUI_PORT="${FREIBURG_WEBUI_PORT:-8077}" FREIBURG_GATEWAY_PORT="${FREIBURG_GATEWAY_PORT:-8090}"
export FREIBURG_SEARCH_PORT="${FREIBURG_SEARCH_PORT:-8083}" FREIBURG_READER_PORT="${FREIBURG_READER_PORT:-18189}"
if [[ -z "$source_mode" ]]; then
  if [[ -n ${FREIBURG_SNAPSHOT:-} ]]; then source_mode=$(realpath -m -- "$FREIBURG_SNAPSHOT")
  elif [[ -s "$R/data/import/textindex/plan.json" ]]; then source_mode=--existing
  elif [[ -f "$HOME/stadt-20261124.tar.gz" ]]; then source_mode="$HOME/stadt-20261124.tar.gz"
  else source_mode=--fresh; fi
fi
config="$R/ops/supervisord.conf"
managed=()
if [[ -S "$R/run/supervisor.sock" && -x "$R/.venv/bin/supervisorctl" ]] && "$R/.venv/bin/supervisorctl" -c "$config" pid >/dev/null 2>&1; then managed=("--managed-ports=$previous_ports"); fi
python3 "$repo/ops/hostcheck.py" --root "$R" "--source=$source_mode" "${managed[@]}"
echo "Datenquelle: $source_mode"
if (( check )); then exit 0; fi
if [[ ! -t 0 && ${#credentials[@]} == 0 ]]; then fail "Interaktives Terminal oder --admin-file / --admin-token-file erforderlich."; fi
if (( ${#credentials[@]} )); then
  python3 - "${credentials[1]}" <<'PY'
import os, sys
from pathlib import Path
path = Path(sys.argv[1]); info = path.stat()
if not path.is_file() or info.st_uid != os.getuid() or info.st_mode & 0o077:
    raise SystemExit("Die Anmeldedatei muss dir gehören und darf nur für dich lesbar sein (chmod 600).")
PY
fi
mkdir -p "$R"/{run,logs,tools}
exec 9>"$R/run/setup.lock"
flock -n 9 || fail "Eine weitere Einrichtung läuft in $R."
export PATH="$R/tools:$PATH"
work=$(mktemp -d "$R/run/setup.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
write_settings() {
  for key in "${settings[@]}"; do
    if [[ -v $key ]]; then printf 'export %s=%q\n' "$key" "${!key}"; fi
  done
}
write_settings >"$work/settings.sh"
signature=$(
  cd "$repo"
  { find app ops eval vendor demo/digitom -type f \
        \( -name '*.py' -o -name '*.sh' -o -name '*.lock' -o -name '*.json' -o -name '*.txt' -o -name '*.conf' -o -name '*.service' \) \
        ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha256sum
    sha256sum setup.sh
    cat "$work/settings.sh"
  } | sha256sum | cut -d' ' -f1
)
unit=agentic-rag-digitom.service
systemd=0
if (( ! no_systemd )) && command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then systemd=1; fi
if (( systemd )); then
  unit_dir="$HOME/.config/systemd/user"
  location="$HOME/.config/agentic-rag-digitom/location.env"
  python3 - "$R" >"$work/location.env" <<'PY'
import sys
print('# Managed by agentic-rag-digitom setup')
print('FREIBURG_ROOT="' + sys.argv[1].replace('\\', '\\\\').replace('"', '\\"') + '"')
PY
  if [[ -e "$location" ]] && ! cmp -s "$work/location.env" "$location"; then fail "Eine andere Installation verwendet bereits diesen Benutzerdienst."; fi
  if [[ -e "$unit_dir/$unit" && ! -e "$location" ]]; then fail "Der Name des Benutzerdienstes ist bereits vergeben."; fi
elif command -v systemctl >/dev/null && systemctl --user is-active --quiet "$unit" 2>/dev/null && (( ${#managed[@]} )); then
  fail "Diese Installation läuft unter systemd. Ohne --no-systemd erneut starten."
fi
digest=4c41191019044dd248de7203f040ab850434382ea175d4c3b28ee63784e12534
if [[ "$source_mode" != --* ]]; then
  actual=$(sha256sum -- "$source_mode" | cut -d' ' -f1)
  [[ "$actual" == "$digest" ]] || fail "Der Snapshot stimmt nicht mit dem geprüften SHA-256 überein."
  if [[ -s "$R/data/import/.snapshot-sha256" && $(<"$R/data/import/.snapshot-sha256") == "$digest" ]]; then source_mode=--existing; fi
fi
install=1
if [[ "$source_mode" == --existing && -s "$R/run/installation.sha256" && $(<"$R/run/installation.sha256") == "$signature" \
      && -x "$STRATA_BIN" && -x "$R/.webui/bin/open-webui" && -x "$R/.venv/bin/supervisord" && -s "$R/app/ui.key" ]]; then install=0; fi
stop_managed() {
  if (( systemd )) && systemctl --user is-active --quiet "$unit"; then systemctl --user stop "$unit" 9>&-
  else bash "$R/ops/services.sh" stop 9>&-; fi
  for ((i=0; i<150; i++)); do [[ -S "$R/run/supervisor.sock" ]] || return 0; sleep 1; done
  fail "Die bisherigen Dienste sind noch aktiv. Protokoll in $R/logs prüfen."
}
if (( install )); then
  if ! command -v uv >/dev/null; then
    curl -fsSL --retry 3 https://github.com/astral-sh/uv/releases/download/0.13.9/uv-x86_64-unknown-linux-gnu.tar.gz -o "$work/uv.tar.gz"
    printf '%s  %s\n' a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5 "$work/uv.tar.gz" | sha256sum -c -
    tar -xzf "$work/uv.tar.gz" -C "$work" uv-x86_64-unknown-linux-gnu/uv
    install -m 700 "$work/uv-x86_64-unknown-linux-gnu/uv" "$R/tools/uv"
  fi
  if ! FREIBURG_PYTHON=$(uv python find 3.12 2>/dev/null); then uv python install 3.12; FREIBURG_PYTHON=$(uv python find 3.12); fi
  export FREIBURG_PYTHON
  if (( ${#managed[@]} )); then stop_managed; managed=(); fi
  bash "$repo/ops/install.sh" "$source_mode" "$digest"
  bash "$R/ops/webui-native-install.sh"
  [[ -x "$STRATA_BIN" ]] || fail "Strata fehlt am erwarteten Pfad $STRATA_BIN."
  printf '%s\n' "$signature" >"$work/installed"
  mv "$work/installed" "$R/run/installation.sha256"
fi
mv "$work/settings.sh" "$R/run/settings.sh"
if (( systemd )); then
  if (( ${#managed[@]} )) && ! systemctl --user is-active --quiet "$unit"; then stop_managed; fi
  mkdir -p "$unit_dir" "$(dirname "$location")"
  install -m 600 "$work/location.env" "$location"
  install -m 644 "$repo/ops/$unit" "$unit_dir/$unit"
  systemctl --user daemon-reload 9>&-
  systemctl --user enable --now "$unit" 9>&-
else
  bash "$R/ops/services.sh" start 9>&-
fi
# Check the authenticated gateway, the reader, the index and Open WebUI before requesting local credentials.
"$R/.venv/bin/python" "$repo/ops/readiness.py" "$R"
"$R/.venv/bin/python" "$R/ops/activate.py" --port "$FREIBURG_WEBUI_PORT" --output "$R/run/abnahme.json" "${credentials[@]}"
echo "DIGITOM ist bereit. Öffne nach Aufbau des SSH-Tunnels http://127.0.0.1:8786/"
printf 'ssh -N -L 127.0.0.1:8786:127.0.0.1:%s BENUTZER@VM\n' "$FREIBURG_WEBUI_PORT"
