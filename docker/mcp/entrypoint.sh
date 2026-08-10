#!/bin/sh
set -e

# Read the same env vars docker-compose.yml sets (with sane standalone
# defaults) so this script also works when the image is run directly.
PORT="${MCP_PORT:-8000}"
TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
DATA_DIR="${CHAMPIONS_MCP_DATA_DIR:-/data}"
DB_PATH="${CHAMPIONS_MCP_DB:-${DATA_DIR}/champions.sqlite}"

# Seed bundled static data (formats.json + any prebuilt champions.sqlite) into
# the persistent volume. -n keeps existing files (never clobber a built DB).
if [ -d /app/data ] && [ "${DATA_DIR}" != "/app/data" ]; then
  mkdir -p "${DATA_DIR}"
  cp -rn /app/data/. "${DATA_DIR}/"
fi

# Build the local mirror on first start (or when CHAMPIONS_MCP_PULL=1 forces a
# refresh). Once champions.sqlite exists the server is fully offline.
if [ ! -f "${DB_PATH}" ] || [ "${CHAMPIONS_MCP_PULL:-0}" = "1" ]; then
  echo "[champions-mcp] Building local data with champions-mcp-pull ..."
  # A failed pull isn't fatal by itself: on a forced refresh
  # (CHAMPIONS_MCP_PULL=1) champions-mcp-pull leaves an already-good
  # existing DB untouched when validation fails, so only bail if that left
  # us with no usable DB at all (e.g. the very first run).
  champions-mcp-pull || echo "[champions-mcp] WARNING: data pull/refresh failed; falling back to existing data if any." >&2
  if [ ! -f "${DB_PATH}" ]; then
    echo "[champions-mcp] ERROR: data pull failed and no database is present." >&2
    exit 1
  fi
fi

# PUBLISHED_PORT (set in docker-compose.yml) carries the *host*-side port so
# the banner below prints a URL that actually works even when MCP_PORT
# remaps the host port away from the container's internal PORT.
EXTERNAL_PORT="${PUBLISHED_PORT:-${PORT}}"
echo ""
echo "================================================================="
echo " champions-mcp v2 ready"
echo " Transport : ${TRANSPORT}"
echo " MCP URL   : http://localhost:${EXTERNAL_PORT}/mcp"
echo " Data      : ${DB_PATH}"
echo "================================================================="
echo ""

# exec replaces this shell with the server process (PID 1), so it receives
# signals (e.g. SIGTERM on `docker stop`) directly instead of via a wrapper.
exec champions-mcp
