#!/usr/bin/env bash
# Operate only the supervisor whose private socket is in this installation.
set -euo pipefail
export FREIBURG_ROOT="${FREIBURG_ROOT:-$(cd "$(dirname "$0")/.." && pwd -P)}"
cd "$FREIBURG_ROOT"
source "$FREIBURG_ROOT/run/settings.sh"
config="$FREIBURG_ROOT/ops/supervisord.conf"
ctl=("$FREIBURG_ROOT/.venv/bin/supervisorctl" -c "$config")
daemon="$FREIBURG_ROOT/.venv/bin/supervisord"
if [[ ${1:-status} == start || ${1:-status} == foreground ]]; then
    # Concurrent authenticated WebUI chats need sockets and database/file handles.
    soft=$(ulimit -Sn)
    hard=$(ulimit -Hn)
    target=8192
    if [[ $hard != unlimited ]] && (( hard < target )); then target=$hard; fi
    if [[ $soft != unlimited ]] && (( soft < target )); then ulimit -Sn "$target"; fi
fi
case "${1:-status}" in
    foreground) exec "$daemon" -n -c "$config" ;;
    start)
        if ! "${ctl[@]}" pid >/dev/null 2>&1; then exec "$daemon" -c "$config"; fi
        for worker in reader search gateway webui; do
            state=$("${ctl[@]}" status "$worker" 2>/dev/null | awk '{print $2}') || true
            if [[ "$state" != RUNNING && "$state" != STARTING ]]; then "${ctl[@]}" start "$worker"; fi
        done
        ;;
    status) exec "${ctl[@]}" status ;;
    stop) exec "${ctl[@]}" shutdown ;;
    *) echo "Aufruf: bash ops/services.sh {status|start|stop|foreground}" >&2; exit 2 ;;
esac
