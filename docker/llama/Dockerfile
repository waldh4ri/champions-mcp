# llama.cpp server (CUDA) — the chat backend, serving Qwen3.6-35B-A3B via its
# OpenAI-compatible API. (Also usable standalone for benchmarking.)
#
# Uses the upstream prebuilt CUDA server image so there's no multi-GB local
# build. The image ships the `llama-server` binary at /app/llama-server; our
# entrypoint wraps it to expose the --n-cpu-moe MoE-offload knob via env vars.
FROM ghcr.io/ggml-org/llama.cpp:server-cuda

# The image ships a CUDA 12.8 toolkit + a forward-compat driver stub
# (libcuda.so.570). On a consumer GeForce with an older host driver (e.g. 550 /
# CUDA 12.4), the forward-compat path is unsupported and CUDA init fails, so the
# model silently runs on the CPU. Removing the compat dir makes the container
# fall back to the host driver injected by the NVIDIA runtime; CUDA minor-version
# compatibility then lets the 12.8 build run on a 12.4 driver. Harmless on hosts
# whose driver is new enough to not need compat anyway.
RUN rm -rf /usr/local/cuda*/compat

COPY docker/llama/entrypoint.sh /entrypoint.sh
# The upstream image may be distroless-ish; ensure the script is executable.
# (COPY preserves mode from the host, but be explicit in case it isn't.)
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
