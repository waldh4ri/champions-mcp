#!/bin/sh
# llama.cpp server for the web chat, in *router mode*: it starts with no model
# loaded and manages models on demand —
#   - every .gguf in ./models (mounted at /models) is listed and loadable;
#   - models added in the web chat (a Hugging Face link) are downloaded in the
#     background into the llama_models volume, with their draft heads (MTP);
#   - a chat request naming a model loads it (unloading the previous one when
#     LLAMA_MODELS_MAX is reached), so switching models needs no restart.
#
# Per-model settings (context, KV cache, GPU layers, MoE offload, reasoning
# budget, speculative decoding) are NOT command-line flags here: flags would
# override the presets. The web chat's Configuration view owns them and writes
# them to /config/presets.ini (the shared chat-data volume), then asks this
# server to reload it.

set -eu

LOG_NAME=llama
if [ -f /usr/local/lib/champions-log.sh ]; then
  . /usr/local/lib/champions-log.sh
else
  log_info() { echo "[llama] $*" >&2; }
  log_warn() { echo "[llama] WARNING: $*" >&2; }
  log_error() { echo "[llama] ERROR: $*" >&2; }
fi

PRESETS=/config/presets.ini

# LLAMA_DEVICE=cpu (the webchat profile's CPU image): inference on CPU + RAM.
# Otherwise (webchat-gpu, CUDA image) the GPU must be visible: without it the
# container would only crawl on the CPU build of CUDA, so idle and say why.
# nvidia-smi works on native Linux and on Docker Desktop/WSL2 (where the GPU
# is /dev/dxg, not /dev/nvidia*).
if [ "${LLAMA_DEVICE:-gpu}" = "cpu" ]; then
  :
elif ! nvidia-smi -L >/dev/null 2>&1 && [ ! -e /dev/nvidia0 ] && [ ! -e /dev/dxg ]; then
  log_info "no NVIDIA GPU visible: local models disabled; idling (the web chat still offers OpenRouter / OpenAI-compatible APIs)" \
    "hint=Docker passes no GPU to this container: check the NVIDIA container runtime / WSL2 GPU support"
  touch /tmp/llama-disabled
  trap 'exit 0' TERM INT
  while :; do sleep 3600 & wait $!; done
fi

# The preset file must exist at startup for llama-server to watch it; the chat
# overwrites it with the real settings as soon as it starts.
if [ ! -f "${PRESETS}" ]; then
  mkdir -p "$(dirname "${PRESETS}")"
  printf 'version = 1\n; placeholder until champions-chat writes the Configuration view settings\n\n[*]\n' > "${PRESETS}"
fi

set -- \
  --host 0.0.0.0 --port "${LLAMA_PORT:-8080}" \
  --models-dir /models \
  --models-preset "${PRESETS}" \
  --models-max "${LLAMA_MODELS_MAX:-1}" \
  --parallel "${LLAMA_PARALLEL:-1}" \
  --jinja                          # use the model's chat template (tool calling)

# Keep the model's reasoning across turns where the template supports it (Qwen3.x).
[ "${LLAMA_REASONING_PRESERVE:-1}" = "1" ] && set -- "$@" --reasoning-preserve

# --load-mode: "none" reads the weights into RAM up front (steadier decode,
# exact load progress); mmap+mlock pins them instead.
if [ "${LLAMA_MLOCK:-0}" = "1" ]; then
  set -- "$@" --load-mode mmap+mlock
elif [ "${LLAMA_NO_MMAP:-1}" = "1" ]; then
  set -- "$@" --load-mode none
fi

# Free-form escape hatch for anything not covered by the Configuration view
# (applies to every model and overrides the presets).
# shellcheck disable=SC2086
[ -n "${LLAMA_EXTRA_ARGS:-}" ] && set -- "$@" ${LLAMA_EXTRA_ARGS}

if [ "${LLAMA_DEVICE:-gpu}" = "cpu" ]; then
  RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2 / 1048576}' /proc/meminfo 2>/dev/null || echo "?")
  GPU="none: CPU inference on $(nproc 2>/dev/null || echo '?') cores, ${RAM_GB} GB RAM (expect a few tokens/s; small models fit best)"
else
  GPU=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -n 1 || true)
fi
LOCAL=$(ls /models/*.gguf 2>/dev/null | wc -l | tr -d ' ')
log_info "starting llama-server (router mode: no model loaded until one is picked)" "gpu=${GPU:-unknown}" \
  "local_gguf_files=${LOCAL}" "models_max=${LLAMA_MODELS_MAX:-1}" "presets=${PRESETS}"
log_info "llama-server args" "args=$*"
exec /app/llama-server "$@"
