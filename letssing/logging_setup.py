"""Logging estruturado (uma linha JSON por evento).

Uso: `log.info("item_added", extra={"fields": {"item_id": 3}})`.
Chaves sensíveis em `fields` são mascaradas por segurança.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_SENSITIVE = ("password", "senha", "secret", "token", "cookie")


def _redact(fields: dict) -> dict:
    return {
        k: ("***" if any(s in k.lower() for s in _SENSITIVE) else v)
        for k, v in fields.items()
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(_redact(fields))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("letssing")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
