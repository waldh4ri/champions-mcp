"""One-line, key=value, optionally colored logging for the docker stack.

Every champions-mcp process (the server, ``champions-mcp-pull``) logs through
here so ``docker compose logs`` reads as one consistent stream across
services, in the same shape the Node sidecars use (``docker/common/log.js``)::

    12:04:31.207 INFO  tool        [3f9c1a] ✓ calc_damage 41ms attacker=Garchomp dmg=48.1-57.0%

- One event per line on stderr, flushed immediately (no buffering surprises
  in ``docker logs``); tracebacks follow indented under their line.
- ``[3f9c1a]`` is the request id of the MCP call being served. It is also sent
  to the calc/sim sidecars (``X-Request-Id``), whose lines carry the same id,
  so one tool call can be followed across containers.
- ``LOG_LEVEL`` (DEBUG/INFO/WARNING/ERROR, default INFO) and ``LOG_COLOR``
  (1/0/auto, default auto: color on a TTY; ``NO_COLOR``/``FORCE_COLOR`` are
  honoured) configure it. docker-compose.yml turns color on, since a
  container's stderr is never a TTY but ``docker compose logs`` renders ANSI.
"""

from __future__ import annotations

import contextvars
import logging
import logging.config
import os
import secrets
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: Id of the MCP request being served (set by the server's logging middleware).
request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_LEVEL_STYLE = {
    "DEBUG": ("DEBUG", "\033[2;37m"),
    "INFO": ("INFO ", "\033[32m"),
    "WARNING": ("WARN ", "\033[33m"),
    "ERROR": ("ERROR", "\033[31m"),
    "CRITICAL": ("FATAL", "\033[1;41;97m"),
}
_NAME_COLOR = "\033[36m"
_RID_COLOR = "\033[35m"
_KEY_COLOR = "\033[2;36m"
_NAME_WIDTH = 11


def new_request_id() -> str:
    """A short random id for correlating one request's log lines."""
    return secrets.token_hex(3)


def color_enabled(stream: Any = None) -> bool:
    """Whether to emit ANSI colors: ``LOG_COLOR`` wins, then NO_COLOR /
    FORCE_COLOR, then whether ``stream`` (stderr) is a terminal."""
    setting = os.environ.get("LOG_COLOR", "auto").strip().lower()
    if setting in ("1", "true", "yes", "on", "always"):
        return True
    if setting in ("0", "false", "no", "off", "never"):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stderr
    return bool(getattr(stream, "isatty", lambda: False)())


def fmt_duration(seconds: float) -> str:
    """Human-friendly elapsed time: 12ms, 3.4s, 2m05s."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _fmt_value(v: Any) -> str:
    if isinstance(v, float):
        v = f"{v:.4g}"
    elif isinstance(v, (list, tuple, set)):
        v = ",".join(str(x) for x in v)
    elif isinstance(v, dict):
        v = ",".join(f"{k}:{x}" for k, x in v.items())
    s = str(v)
    if s == "" or any(c in s for c in ' "=\n\t'):
        s = '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return s


class Formatter(logging.Formatter):
    """``HH:MM:SS.mmm LEVEL name [rid] message key=value ...``"""

    def __init__(self, color: bool | None = None) -> None:
        super().__init__()
        self.color = color_enabled() if color is None else color

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self.color else text

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}"
        label, lcolor = _LEVEL_STYLE.get(record.levelname, (record.levelname[:5].ljust(5), ""))
        name = record.name
        for prefix in ("champions_mcp.", "champions-mcp."):
            if name.startswith(prefix):
                name = name[len(prefix):]
        if name.startswith("uvicorn"):
            name = "http"
        parts = [self._c(_DIM, ts), self._c(lcolor, label), self._c(_NAME_COLOR, name.ljust(_NAME_WIDTH))]
        rid = getattr(record, "rid", None) or request_id.get()
        if rid:
            parts.append(self._c(_RID_COLOR, f"[{rid}]"))
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            msg = self._c(lcolor, msg)
        elif getattr(record, "bold", False):
            msg = self._c(_BOLD, msg)
        parts.append(msg)
        fields = getattr(record, "fields", None)
        if fields:
            parts.extend(
                f"{self._c(_KEY_COLOR, k + '=')}{_fmt_value(v)}"
                for k, v in fields.items() if v is not None
            )
        line = " ".join(parts)
        if record.exc_info:
            tb = self.formatException(record.exc_info)
            line += "\n" + "\n".join("    " + ln for ln in tb.splitlines())
        return line


class Log:
    """Thin logger wrapper taking structured fields as keyword arguments:
    ``log.info("pull finished", took="3.2s", pokemon=261)``."""

    def __init__(self, name: str) -> None:
        self.logger = logging.getLogger(name)

    def _log(self, level: int, msg: str, args: tuple, exc_info: Any, bold: bool,
             fields: dict[str, Any]) -> None:
        if self.logger.isEnabledFor(level):
            self.logger.log(level, msg, *args, exc_info=exc_info, stacklevel=3,
                            extra={"fields": fields, "bold": bold})

    def debug(self, msg: str, *args: Any, exc_info: Any = None, bold: bool = False, **fields: Any) -> None:
        self._log(logging.DEBUG, msg, args, exc_info, bold, fields)

    def info(self, msg: str, *args: Any, exc_info: Any = None, bold: bool = False, **fields: Any) -> None:
        self._log(logging.INFO, msg, args, exc_info, bold, fields)

    def warning(self, msg: str, *args: Any, exc_info: Any = None, bold: bool = False, **fields: Any) -> None:
        self._log(logging.WARNING, msg, args, exc_info, bold, fields)

    def error(self, msg: str, *args: Any, exc_info: Any = None, bold: bool = False, **fields: Any) -> None:
        self._log(logging.ERROR, msg, args, exc_info, bold, fields)

    def is_debug(self) -> bool:
        return self.logger.isEnabledFor(logging.DEBUG)


def get(name: str) -> Log:
    """A :class:`Log` for ``name`` (conventionally a short component name)."""
    return Log(name if name.startswith("champions_mcp") else f"champions_mcp.{name}")


@contextmanager
def timed(log: Log, what: str, *, start_level: int = logging.INFO, **fields: Any) -> Iterator[dict[str, Any]]:
    """Log ``▶ what`` on entry and ``✓ what took=...`` on exit (``✗`` + the
    error on failure). The yielded dict's entries are added to the exit line,
    so the block can report what it did."""
    out: dict[str, Any] = {}
    t0 = time.perf_counter()
    if log.logger.isEnabledFor(start_level):
        log.logger.log(start_level, f"▶ {what}", stacklevel=3, extra={"fields": fields})
    try:
        yield out
    except BaseException as exc:
        log.error(f"✗ {what} failed", took=fmt_duration(time.perf_counter() - t0),
                  error=f"{type(exc).__name__}: {exc}", **fields)
        raise
    log.info(f"✓ {what}", took=fmt_duration(time.perf_counter() - t0), **fields, **out)


class Progress:
    """Progress for a long loop, logged at most once every ``every`` seconds
    (so a cached, instant run stays quiet and a slow, rate-limited one shows
    it is alive): ``… limitless events progress=40/150 teams=812``."""

    def __init__(self, log: Log, what: str, total: int | None = None, every: float = 10.0) -> None:
        self.log, self.what, self.total, self.every = log, what, total, every
        self.done = 0
        self._t0 = self._last = time.perf_counter()

    def tick(self, n: int = 1, **fields: Any) -> None:
        self.done += n
        now = time.perf_counter()
        if now - self._last >= self.every:
            self._last = now
            prog = f"{self.done}/{self.total}" if self.total else str(self.done)
            self.log.info(f"… {self.what}", progress=prog, elapsed=fmt_duration(now - self._t0), **fields)


class DropSuccessfulAccess(logging.Filter):
    """uvicorn access lines for 2xx/3xx responses are pure noise next to the
    tool-call lines (every MCP message is a POST /mcp); keep only failures."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        try:
            return int(args[4]) >= 400
        except (IndexError, TypeError, ValueError):
            return True


class DropUvicornBoilerplate(logging.Filter):
    """uvicorn's fixed startup lines say nothing the server's own "ready"
    line doesn't (with the real, published URL); keep everything else."""

    _STATIC = ("Started server process", "Waiting for application startup",
               "Application startup complete", "Uvicorn running on")

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or not str(record.msg).startswith(self._STATIC)


def _route_uvicorn_through_root() -> None:
    """Make uvicorn's loggers propagate to our root handler instead of
    installing its own formatters. The MCP SDK builds ``uvicorn.Config`` later
    with uvicorn's default ``LOGGING_CONFIG``, so that dict is rewritten in
    place; it is also applied now, for an app that uvicorn's CLI already
    configured logging for before importing it (the chat bridge)."""
    try:
        import uvicorn.config as uvconfig
    except ImportError:
        return
    plain = {"()": "logging.Formatter"}
    uvconfig.LOGGING_CONFIG.clear()
    uvconfig.LOGGING_CONFIG.update({
        "version": 1,
        "disable_existing_loggers": False,
        # kept (unused) because uvicorn writes use_colors into these two keys
        "formatters": {"default": dict(plain), "access": dict(plain)},
        "filters": {"drop_ok": {"()": f"{__name__}.DropSuccessfulAccess"},
                    "drop_static": {"()": f"{__name__}.DropUvicornBoilerplate"}},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True, "level": "INFO"},
            "uvicorn.error": {"handlers": [], "propagate": True, "filters": ["drop_static"]},
            "uvicorn.access": {"handlers": [], "propagate": True, "filters": ["drop_ok"]},
        },
    })
    logging.config.dictConfig(uvconfig.LOGGING_CONFIG)


def setup(level: str | None = None) -> None:
    """Install the formatter on the root logger (replacing any handler a
    library added first) and quiet chatty third-party loggers."""
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    if level_name == "WARN":
        level_name = "WARNING"
    numeric = getattr(logging, level_name, logging.INFO)

    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(errors="backslashreplace", line_buffering=True)
        except (ValueError, OSError):  # pragma: no cover - exotic streams
            pass
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Formatter())
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(numeric)
    logging.captureWarnings(True)

    # Per-request HTTP client lines and the SDK's own per-message chatter only
    # under DEBUG; the server's middleware logs the meaningful version.
    quiet = logging.DEBUG if numeric <= logging.DEBUG else logging.WARNING
    for name in ("httpx", "httpcore", "mcp", "sse_starlette", "asyncio", "urllib3"):
        logging.getLogger(name).setLevel(quiet)
    _route_uvicorn_through_root()
