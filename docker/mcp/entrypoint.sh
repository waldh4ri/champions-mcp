#!/bin/sh
set -e

# Logging helpers (log_info/log_warn/log_error), same line format as the
# Python and Node services. Fall back to plain echo if run outside the image.
LOG_NAME=entrypoint
if [ -f /usr/local/lib/champions-log.sh ]; then
  . /usr/local/lib/champions-log.sh
else
  log_info() { echo "[champions-mcp] $*" >&2; }
  log_warn() { echo "[champions-mcp] WARNING: $*" >&2; }
  log_error() { echo "[champions-mcp] ERROR: $*" >&2; }
fi

# Read the same env vars docker-compose.yml sets (with sane standalone
# defaults) so this script also works when the image is run directly.
TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
DATA_DIR="${CHAMPIONS_MCP_DATA_DIR:-/data}"
DB_PATH="${CHAMPIONS_MCP_DB:-${DATA_DIR}/champions.sqlite}"

log_info "container starting" "version=$(python -c 'import importlib.metadata as m; print(m.version("champions-mcp"))' 2>/dev/null || echo '?')" \
  "transport=${TRANSPORT}" "db=${DB_PATH}" "max_data_age_days=${CHAMPIONS_MCP_MAX_DATA_AGE_DAYS:-7}"

# Seed bundled static data (formats.json + any prebuilt champions.sqlite) into
# the persistent volume. -n keeps existing files (never clobber a built DB).
if [ -d /app/data ] && [ "${DATA_DIR}" != "/app/data" ]; then
  mkdir -p "${DATA_DIR}"
  cp -rn /app/data/. "${DATA_DIR}/"
fi

# Leftovers of a pull that was interrupted (container stopped mid-build) are
# never valid; a new pull starts from scratch anyway.
for leftover in "${DB_PATH%.sqlite}.sqlite.tmp" "${DB_PATH%.sqlite}.sqlite.buildsrefresh.tmp"; do
  if [ -f "$leftover" ]; then
    log_warn "removing leftovers of an interrupted pull" "file=${leftover}"
    rm -f "$leftover"
  fi
done

# Decide what data work is needed. Only what the server cannot start without
# runs here, blocking; the slow rest runs in the background while it serves
# (CHAMPIONS_MCP_BACKGROUND_PULL, see champions_mcp/refresh.py):
#   no / unreadable database   core pull now (~2 min), corpus in the background
#   built by older code        same: the old data may lack what this code reads
#   older than MAX_DATA_AGE    serve it; full refresh in the background
#   CHAMPIONS_MCP_PULL=1       serve it; full refresh in the background
# The observed-team corpus is the slow part: Limitless rate-limits
# unauthenticated clients to 50 requests / 5 min (~15 min cold; later pulls
# reuse the pull cache). CHAMPIONS_MCP_BUILDS_SKIP=1 skips it entirely.
BACKGROUND=""
CORE_PULL=0
if [ ! -f "${DB_PATH}" ]; then
  log_info "no database yet (first start): building the core data before serving"
  CORE_PULL=1
elif [ "${CHAMPIONS_MCP_PULL:-0}" = "1" ]; then
  log_info "refresh forced by CHAMPIONS_MCP_PULL=1: serving the current data, full refresh in the background"
  BACKGROUND=full
elif ! REASON=$(python -m champions_mcp.pull.freshness "${DB_PATH}"); then
  case "${REASON}" in
    "data format"*|"unreadable"*)
      log_warn "data was built by older code; rebuilding the core data before serving" "reason=${REASON}"
      CORE_PULL=1 ;;
    *)
      log_info "data is stale: serving it, full refresh in the background" "reason=${REASON}"
      BACKGROUND=full ;;
  esac
else
  log_info "data is fresh; no pull needed" "status=${REASON}"
  # A first start fetches the corpus in the background; if a restart cut that
  # short, the data looks fresh but has no observed teams: resume it.
  if [ "${CHAMPIONS_MCP_BUILDS_SKIP:-0}" != "1" ] && ! python -m champions_mcp.pull.freshness --corpus "${DB_PATH}" >/dev/null; then
    log_warn "the observed-team corpus is missing (an earlier background fetch was interrupted); fetching it in the background"
    BACKGROUND=corpus
  fi
fi

if [ "${CORE_PULL}" = "1" ]; then
  if [ -f "${DATA_DIR}/pull-cache.sqlite" ]; then CACHE_STATE="warm"; else CACHE_STATE="cold"; fi
  log_info "running champions-mcp-pull --skip-builds (core data)" "cache=${CACHE_STATE}"
  # A failed pull leaves any existing database untouched; only bail when that
  # leaves no database at all (e.g. the very first run).
  if ! champions-mcp-pull --skip-builds; then
    log_warn "core data pull failed; falling back to the existing data"
  fi
  if [ ! -f "${DB_PATH}" ]; then
    log_error "data pull failed and no database is present; exiting" "db=${DB_PATH}"
    exit 1
  fi
  if [ "${CHAMPIONS_MCP_BUILDS_SKIP:-0}" != "1" ]; then
    BACKGROUND=corpus
  fi
fi
if [ "${CHAMPIONS_MCP_BUILDS_SKIP:-0}" = "1" ] && [ "${BACKGROUND}" = "corpus" ]; then
  BACKGROUND=""
fi
export CHAMPIONS_MCP_BACKGROUND_PULL="${BACKGROUND}"

# The server logs its own "ready" line (with the published MCP URL) once
# it is actually listening; see champions_mcp/server_log.py.
log_info "starting MCP server" "background_refresh=${BACKGROUND:-none}"

# exec replaces this shell with the server process (PID 1), so it receives
# signals (e.g. SIGTERM on `docker stop`) directly instead of via a wrapper.
exec champions-mcp
