# shellcheck shell=sh
# Entrypoint logging in the same line shape as champions_mcp/log.py and
# docker/common/log.js, so `docker compose logs` reads as one stream:
#
#   12:04:31.207 INFO  entrypoint  data is stale, refreshing reason="built 9 days ago (max 7)"
#
# Usage (after `. /usr/local/lib/champions-log.sh`):
#   LOG_NAME=entrypoint
#   log_info  "message" key=value "key=value with spaces"
#   log_warn / log_error / log_debug
# LOG_LEVEL / LOG_COLOR / NO_COLOR / FORCE_COLOR behave as in log.py.

LOG_NAME="${LOG_NAME:-entrypoint}"

case "$(printf '%s' "${LOG_COLOR:-auto}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on|always) _LOG_COLOR=1 ;;
  0|false|no|off|never) _LOG_COLOR=0 ;;
  *)
    if [ -n "${NO_COLOR:-}" ]; then _LOG_COLOR=0
    elif [ -n "${FORCE_COLOR:-}" ]; then _LOG_COLOR=1
    elif [ -t 2 ]; then _LOG_COLOR=1
    else _LOG_COLOR=0
    fi ;;
esac

case "$(printf '%s' "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')" in
  debug) _LOG_MIN=10 ;; warn|warning) _LOG_MIN=30 ;; error) _LOG_MIN=40 ;; *) _LOG_MIN=20 ;;
esac

_log_c() { [ "${_LOG_COLOR}" = 1 ] && printf '\033[%sm%s\033[0m' "$1" "$2" || printf '%s' "$2"; }

_log_ts() {
  # GNU date has %N; busybox date doesn't (falls back to .000).
  ts=$(date +%H:%M:%S.%3N 2>/dev/null)
  case "$ts" in *N|*%*|"") ts="$(date +%H:%M:%S).000" ;; esac
  printf '%s' "$ts"
}

# _log LEVELNUM LABEL COLOR message [key=value ...]
_log() {
  num=$1; label=$2; color=$3; msg=$4; shift 4
  [ "$num" -lt "${_LOG_MIN}" ] && return 0
  if [ "$num" -ge 30 ]; then msg=$(_log_c "$color" "$msg"); fi
  line="$(_log_c 2 "$(_log_ts)") $(_log_c "$color" "$label") $(_log_c 36 "$(printf '%-11s' "$LOG_NAME")") $msg"
  for kv in "$@"; do
    key=${kv%%=*}; val=${kv#*=}
    case "$val" in *" "*|"") val="\"$val\"" ;; esac
    line="$line $(_log_c '2;36' "$key=")$val"
  done
  printf '%s\n' "$line" >&2
}

log_debug() { m=$1; shift; _log 10 "DEBUG" '2;37' "$m" "$@"; }
log_info()  { m=$1; shift; _log 20 "INFO " 32 "$m" "$@"; }
log_warn()  { m=$1; shift; _log 30 "WARN " 33 "$m" "$@"; }
log_error() { m=$1; shift; _log 40 "ERROR" 31 "$m" "$@"; }
