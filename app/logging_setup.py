import json
import logging
import logging.handlers
import os
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import LOG_BACKUP_COUNT, LOG_DIR, LOG_FORMAT, LOG_MAX_BYTES

# Per-request trace ID, propagated through the logger via a filter.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Reserved LogRecord attribute names — used to skip/prefix them in JSON output.
_RESERVED_LOG_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


class RequestIdFilter(logging.Filter):
    """Injects request_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One-line JSON per record; passes ``extra=`` kwargs through as top-level fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _RESERVED_LOG_ATTRS or k.startswith("_") or k in payload:
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Logger construction
# ---------------------------------------------------------------------------
os.makedirs(LOG_DIR, exist_ok=True)

_log_format = "%(asctime)s - %(request_id)s - %(name)s - %(levelname)s - %(message)s"

logger: logging.Logger = logging.getLogger("pokering")
logger.setLevel(logging.INFO)

# Console handler — warnings and above to keep terminal clean
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)
_console_handler.setFormatter(logging.Formatter(_log_format))
_console_handler.addFilter(RequestIdFilter())

# File handler with rotation — info and above for audit trail
_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "pokering.log"),
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(_log_format))
_file_handler.addFilter(RequestIdFilter())

if LOG_FORMAT == "json":
    _file_handler.setFormatter(JsonFormatter())

logger.addHandler(_console_handler)
logger.addHandler(_file_handler)


# ---------------------------------------------------------------------------
# audit() helper
# ---------------------------------------------------------------------------
def audit(event: str, **fields: object) -> None:
    """Emit a structured audit event.

    Text mode: ``event=X k=v k=v``.
    JSON mode: extras become top-level fields.
    Keys colliding with stdlib LogRecord attrs are prefixed with ``x_`` so
    they survive JSON-mode formatting (record.__dict__ iteration skips
    reserved keys).
    """
    clean: dict[str, object] = {}
    for k, v in fields.items():
        if v is None:
            continue
        safe_key = f"x_{k}" if k in _RESERVED_LOG_ATTRS else k
        clean[safe_key] = v
    parts = [f"{k}={v}" for k, v in clean.items()]
    msg = f"event={event}" + (" " + " ".join(parts) if parts else "")
    logger.info(msg, extra={"event": event, **clean})
