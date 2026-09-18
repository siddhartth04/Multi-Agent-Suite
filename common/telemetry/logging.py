"""Structured JSON logging correlated with the active trace."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from common.telemetry.context import get_trace_context

_CONFIGURED = False

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line, stamped with trace/request ids."""

    def __init__(self, service_id: str = "unknown", module_id: str = "unknown") -> None:
        super().__init__()
        self.service_id = service_id
        self.module_id = module_id

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_trace_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service_id": self.service_id,
            "module_id": self.module_id,
            "trace_id": ctx.trace_id,
            "request_id": ctx.request_id,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def configure_logging(service_id: str, module_id: str, level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger. Idempotent."""
    global _CONFIGURED

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if _CONFIGURED:
        for handler in root.handlers:
            if isinstance(handler.formatter, JsonFormatter):
                handler.formatter.service_id = service_id
                handler.formatter.module_id = module_id
        return

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_id=service_id, module_id=module_id))
    root.addHandler(handler)

    # uvicorn ships its own handlers; route them through ours instead
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
