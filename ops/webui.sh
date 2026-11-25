#!/usr/bin/env bash
# Linux host networking lets the official UI reach the private gateway on loopback.
set -euo pipefail
R="${FREIBURG_ROOT:-/srv/freiburg-openrag}"
export OPENAI_API_BASE_URL="${FREIBURG_GATEWAY_URL:-http://127.0.0.1:${FREIBURG_GATEWAY_PORT:-8090}/v1}" OPENAI_API_KEY
OPENAI_API_KEY="$(cat "$R/app/chat.key")"
export PORT="${FREIBURG_WEBUI_PORT:-8077}" HOST=127.0.0.1 DATA_DIR="${FREIBURG_WEBUI_DATA:-$R/webui-data}"
export WEBUI_AUTH="${WEBUI_AUTH:-True}" ENABLE_SIGNUP="${ENABLE_SIGNUP:-False}"
export ENABLE_OLLAMA_API=False ENABLE_OPENAI_API=True ENABLE_PERSISTENT_CONFIG="${ENABLE_PERSISTENT_CONFIG:-True}"
export FREIBURG_UI_SIGN_KEY_FILE="$R/app/ui.key"
export RAG_EMBEDDING_MODEL="$R/models/webui-embedding" RAG_EMBEDDING_ENGINE=""
export RAG_EMBEDDING_QUERY_PREFIX="query: " RAG_EMBEDDING_CONTENT_PREFIX="passage: "
export USE_CUDA_DOCKER=false HF_HUB_OFFLINE=1
export OFFLINE_MODE=True RAG_EMBEDDING_MODEL_AUTO_UPDATE=False RAG_RERANKING_MODEL_AUTO_UPDATE=False
export ENABLE_VERSION_UPDATE_CHECK=False ENABLE_EVALUATION_ARENA_MODELS=False
export ENABLE_TITLE_GENERATION=False ENABLE_FOLLOW_UP_GENERATION=False ENABLE_TAGS_GENERATION=False
mkdir -p -m 700 "$DATA_DIR"
if [[ "${FREIBURG_WEBUI_NATIVE:-0}" == 1 ]]; then
    static="$("$R/.webui/bin/python" -c "import sysconfig; print(sysconfig.get_path('purelib'))")/open_webui/static/freiburg"
    "$R/.venv/bin/python" "$R/ops/ui_images.py" "$static"
    exec "$R/.webui/bin/open-webui" serve --host "$HOST" --port "$PORT"
fi
"$R/.venv/bin/python" "$R/ops/ui_images.py" "$R/ui-public"
image=ghcr.io/open-webui/open-webui@sha256:0c4439e78c24c85e3e7a41936771d3b02f0df38daf28a086cd83ca74804f7676
exec docker run --rm --name freiburg-stadtchat-ui --network host \
    --mount "type=bind,src=$DATA_DIR,dst=/app/backend/data" \
    --mount "type=bind,src=$R/ui-public,dst=/app/backend/open_webui/static/freiburg,readonly" \
    --mount "type=bind,src=$R/app/ui.key,dst=/run/secrets/freiburg-ui,readonly" \
    --mount "type=bind,src=$R/models/webui-embedding,dst=/opt/freiburg-embedding,readonly" \
    -e FREIBURG_UI_SIGN_KEY_FILE=/run/secrets/freiburg-ui -e RAG_EMBEDDING_MODEL=/opt/freiburg-embedding \
    -e RAG_EMBEDDING_QUERY_PREFIX -e RAG_EMBEDDING_CONTENT_PREFIX -e RAG_EMBEDDING_ENGINE -e USE_CUDA_DOCKER -e HF_HUB_OFFLINE \
    -e HOST -e PORT -e WEBUI_AUTH -e ENABLE_SIGNUP -e OPENAI_API_BASE_URL -e OPENAI_API_KEY \
    -e ENABLE_OPENAI_API -e ENABLE_OLLAMA_API -e ENABLE_PERSISTENT_CONFIG -e OFFLINE_MODE \
    -e RAG_EMBEDDING_MODEL_AUTO_UPDATE -e RAG_RERANKING_MODEL_AUTO_UPDATE -e ENABLE_VERSION_UPDATE_CHECK \
    -e ENABLE_EVALUATION_ARENA_MODELS -e ENABLE_TITLE_GENERATION -e ENABLE_FOLLOW_UP_GENERATION -e ENABLE_TAGS_GENERATION \
    --entrypoint /bin/bash "$image" -c 'exec uvicorn open_webui.main:app --host "$HOST" --port "$PORT"'
