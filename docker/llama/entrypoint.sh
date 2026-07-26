#!/bin/sh
# llama.cpp server launcher for the benchmark stack.

set -eu

PORT="${LLAMA_PORT:-8080}"        # container-side listen port
CTX="${LLAMA_CTX_SIZE:-32768}"    # context window (tokens)
NGL="${LLAMA_N_GPU_LAYERS:-99}"   # number of layers offloaded to GPU
# KV-cache quant.
KV_K="${LLAMA_CACHE_TYPE_K:-q8_0}"
KV_V="${LLAMA_CACHE_TYPE_V:-q8_0}"
# unsloth's Qwen3.6-35B-A3B-MTP-GGUF card recommends `-fa on -np 1` explicitly
# rather than relying on flash-attn "auto" detection; -np 1 is LLAMA_PARALLEL's
# default below.
FA="${LLAMA_FLASH_ATTN:-on}"

set -- \
  --host 0.0.0.0 --port "${PORT}" \
  --alias "${LLAMA_ALIAS:-local-model}" \
  --ctx-size "${CTX}" \
  --n-gpu-layers "${NGL}" \
  --cache-type-k "${KV_K}" \
  --cache-type-v "${KV_V}" \
  --flash-attn "${FA}" \
  --parallel "${LLAMA_PARALLEL:-1}" \
  --jinja                          # use the model's chat template (tool calling)

if [ -n "${LLAMA_N_CPU_MOE:-}" ]; then
  set -- "$@" --n-cpu-moe "${LLAMA_N_CPU_MOE}"
fi

# Qwen3.6's template advertises supports_preserve_reasoning
[ "${LLAMA_REASONING_PRESERVE:-1}" = "1" ] && set -- "$@" --reasoning-preserve

# Cap thinking tokens per generation so the model plans briefly instead of
# musing for thousands of tokens each tool round (keeps turns snappy). -1 =
# unrestricted, 0 = no thinking. Set LLAMA_REASONING_BUDGET=off on old builds
# that lack the flag. No effect when thinking is disabled.
case "${LLAMA_REASONING_BUDGET:-}" in
  ""|off|none) : ;;
  *) set -- "$@" --reasoning-budget "${LLAMA_REASONING_BUDGET}" ;;
esac

# --no-mmap + --mlock force the weights resident in RAM (steadier offload perf).
[ "${LLAMA_NO_MMAP:-1}" = "1" ] && set -- "$@" --no-mmap
[ "${LLAMA_MLOCK:-0}" = "1" ]  && set -- "$@" --mlock

# Optional MTP/speculative decoding using a draft model.
# Kept opt-in because performance depends heavily on hardware/model pairing.
if [ "${LLAMA_MTP_ENABLED:-0}" = "1" ]; then
  set -- "$@" --spec-type "${LLAMA_MTP_SPEC_TYPE:-draft-mtp}"

  if [ -n "${LLAMA_MTP_MODEL_HF:-}" ]; then
    set -- "$@" --spec-draft-hf "${LLAMA_MTP_MODEL_HF}"
  elif [ -n "${LLAMA_MTP_MODEL_FILE:-}" ]; then
    set -- "$@" --spec-draft-model "/models/${LLAMA_MTP_MODEL_FILE}"
  fi

  [ -n "${LLAMA_MTP_DRAFT_MIN:-}" ] && set -- "$@" --spec-draft-n-min "${LLAMA_MTP_DRAFT_MIN}"
  [ -n "${LLAMA_MTP_DRAFT_MAX:-}" ] && set -- "$@" --spec-draft-n-max "${LLAMA_MTP_DRAFT_MAX}"
  [ -n "${LLAMA_MTP_P_MIN:-}" ] && set -- "$@" --spec-draft-p-min "${LLAMA_MTP_P_MIN}"
  # shellcheck disable=SC2086
  [ -n "${LLAMA_MTP_EXTRA_ARGS:-}" ] && set -- "$@" ${LLAMA_MTP_EXTRA_ARGS}
fi

# Free-form escape hatch for anything not parameterised above.
# shellcheck disable=SC2086
[ -n "${LLAMA_EXTRA_ARGS:-}" ] && set -- "$@" ${LLAMA_EXTRA_ARGS}

# Model source: a Hugging Face ref (auto-downloaded + cached to the volume) takes
# precedence; otherwise a GGUF mounted into /models. Exactly one must be set.
if [ -n "${LLAMA_MODEL_HF:-}" ]; then
  set -- "$@" -hf "${LLAMA_MODEL_HF}"
elif [ -n "${LLAMA_MODEL_FILE:-}" ]; then
  set -- "$@" -m "/models/${LLAMA_MODEL_FILE}"
else
  echo "[llama] ERROR: set LLAMA_MODEL_HF (e.g. unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M)" >&2
  echo "[llama]        or drop a .gguf in ./models and set LLAMA_MODEL_FILE." >&2
  exit 1
fi

echo "[llama] launching: llama-server $*" >&2
exec /app/llama-server "$@"
