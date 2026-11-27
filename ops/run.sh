#!/usr/bin/env bash
set -euo pipefail
export FREIBURG_ROOT="${FREIBURG_ROOT:-/srv/freiburg-openrag}"
R="$FREIBURG_ROOT"
[[ ! -f "$R/run/settings.sh" ]] || source "$R/run/settings.sh"
export PYTHONPATH="$R/app:$R/vendor"
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-3}" OPENBLAS_NUM_THREADS="${FREIBURG_BLAS_THREADS:-3}"
export HF_HUB_OFFLINE=1
case "${1:-}" in
  search)
    export CUDA_VISIBLE_DEVICES="${FREIBURG_SEARCH_GPU:-3}" FREIBURG_SEARCH_DEVICE="${FREIBURG_SEARCH_DEVICE:-cuda:0}"
    exec "$R/.venv/bin/python" -u "$R/app/topk_city.py" serve "$R/data/import"
    ;;
  reader)
    assets="${STRATA_ASSETS:-$R/assets}"
    shard="$assets/models/unsloth-UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf"
    test -f "$shard"
    export STRATA_FEED="https://github.com/Niko1221/Strata/releases/download/v0.2.21"
    export STRATA_RAG_READER_KEY
    STRATA_RAG_READER_KEY="$(cat "$R/app/reader.key")"
    strata="${STRATA_BIN:-$HOME/.local/bin/strata}"
    stop_reader() { "$strata" launch --stop >/dev/null 2>&1 || true; }
    trap 'stop_reader; exit 0' TERM INT
    "$strata" launch \
      --family unsloth --model UD-Q4_K_XL --model-path "$shard" --data-dir "$assets" \
      --backend nvidia --gpu "${FREIBURG_READER_GPUS:-0,1,2,3}" --split-engine off \
      --context "${FREIBURG_CONTEXT:-16k}" --parallel "${FREIBURG_MODEL_SLOTS:-4}" \
      --drafting "${FREIBURG_DRAFTING:-mtp}" --vision cpu --access local --port "${FREIBURG_READER_PORT:-18189}" \
      --api-key-env STRATA_RAG_READER_KEY --yes --no-tui
    port="${FREIBURG_READER_PORT:-18189}"
    failures=0
    while true; do
      if curl -fsS --max-time 10 -o /dev/null -H "Authorization: Bearer $STRATA_RAG_READER_KEY" "http://127.0.0.1:$port/v1/models"; then
        failures=0
      else
        ((failures+=1))
        if ((failures >= 6)); then stop_reader; exit 1; fi
      fi
      sleep 5
    done
    ;;
  gateway)
    export CUDA_VISIBLE_DEVICES="${FREIBURG_RERANK_GPU:-2}"
    export FREIBURG_RERANK_DEVICE="${FREIBURG_RERANK_DEVICE:-cuda:0}" FREIBURG_VISION_DEVICE="${FREIBURG_VISION_DEVICE:-cpu}"
    exec "$R/.venv/bin/uvicorn" city_gateway:app --app-dir "$R/app" --host 127.0.0.1 --port "${FREIBURG_GATEWAY_PORT:-8090}"
    ;;
  webui)
    export FREIBURG_WEBUI_NATIVE=1 WEBUI_AUTH=True ENABLE_SIGNUP=False
    exec bash "$R/ops/webui.sh"
    ;;
  *) echo "Usage: bash ops/run.sh {search|reader|gateway|webui}" >&2; exit 2 ;;
esac
