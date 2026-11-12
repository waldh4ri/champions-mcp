# llama.cpp server for the web chat (router mode, see entrypoint.sh).
#
# Uses the upstream prebuilt server images, so there's no multi-GB local build;
# both ship the `llama-server` binary at /app/llama-server.
#   BASE_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda   NVIDIA GPU (webchat-gpu)
#   BASE_IMAGE=ghcr.io/ggml-org/llama.cpp:server        CPU + RAM   (webchat)
ARG BASE_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda
FROM ${BASE_IMAGE}

# The image ships a CUDA 12.8 toolkit + a forward-compat driver stub
# (libcuda.so.570). On a consumer GeForce with an older host driver (e.g. 550 /
# CUDA 12.4), the forward-compat path is unsupported and CUDA init fails, so the
# model silently runs on the CPU. Removing the compat dir makes the container
# fall back to the host driver injected by the NVIDIA runtime; CUDA minor-version
# compatibility then lets the 12.8 build run on a 12.4 driver. Harmless on hosts
# whose driver is new enough to not need compat anyway.
RUN rm -rf /usr/local/cuda*/compat  # no-op on the CPU image

COPY docker/common/log.sh /usr/local/lib/champions-log.sh
COPY docker/llama/entrypoint.sh /entrypoint.sh
# Strip CRs in case the checkout converted line endings (Windows + autocrlf).
RUN sed -i 's/\r$//' /entrypoint.sh /usr/local/lib/champions-log.sh
# The upstream image may be distroless-ish; ensure the script is executable.
# (COPY preserves mode from the host, but be explicit in case it isn't.)
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
