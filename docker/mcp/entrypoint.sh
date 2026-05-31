#!/bin/sh
set -e

HOST="${MCP_HOST:-0.0.0.0}"
PORT="${MCP_PORT:-8000}"
TRANSPORT="${MCP_TRANSPORT:-streamable-http}"

# ---------------------------------------------------------------------------
# Seed bundled static data (regulations, Pokémon JSON, etc.) into the
# persistent data directory.  Uses -n so existing files are never overwritten,
# preserving any user-edited regulation files across restarts.
# ---------------------------------------------------------------------------
DATA_DIR="${CHAMPIONS_MCP_DATA_DIR:-/data}"
if [ -d /app/data ] && [ "${DATA_DIR}" != "/app/data" ]; then
  mkdir -p "${DATA_DIR}"
  cp -rn /app/data/. "${DATA_DIR}/"
fi

# ---------------------------------------------------------------------------
# Optional prewarm: cache PokeAPI mirror + name index on first run.
# Set CHAMPIONS_MCP_PREWARM=0 to skip (useful when data/ is pre-populated).
# ---------------------------------------------------------------------------
if [ "${CHAMPIONS_MCP_PREWARM:-1}" = "1" ]; then
  echo "[champions-mcp] Running prewarm (set CHAMPIONS_MCP_PREWARM=0 to skip)..."
  champions-mcp-prewarm || echo "[champions-mcp] WARNING: prewarm exited non-zero; continuing anyway."
fi

# ---------------------------------------------------------------------------
# Print the MCP server URL so users / MCP clients know where to connect.
# ---------------------------------------------------------------------------
EXTERNAL_PORT="${PUBLISHED_PORT:-${PORT}}"
echo ""
echo "================================================================="
echo " champions-mcp ready"
echo " Transport : ${TRANSPORT}"
echo " MCP URL   : http://localhost:${EXTERNAL_PORT}/mcp"
echo ""
echo " Add to VS Code (settings.json):"
echo '  "mcp": {'
echo '    "servers": {'
echo '      "champions-mcp": {'
echo '        "type": "http",'
echo "        \"url\": \"http://localhost:${EXTERNAL_PORT}/mcp\""
echo '      }'
echo '    }'
echo '  }'
echo "================================================================="
echo ""

exec champions-mcp
