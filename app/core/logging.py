"""Structured JSON logging with per-request correlation IDs.

The correlation id is stored in a ContextVar so any log line emitted while handling
a request automatically carries `request_id` without threading it through calls.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

from app.core.config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        )
    )
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Quiet noisy access logger; our middleware logs requests structurally.
    logging.getLogger("uvicorn.access").handlers.clear()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
