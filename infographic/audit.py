"""Structured audit logging (no prompts, no secrets)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

_audit_logger = logging.getLogger("uab_infographic_audit")
_audit_logger.setLevel(logging.INFO)
if not _audit_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(_h)


def audit_log(
    session_id: str,
    provider: str,
    style: str,
    audience: str,
    success: bool,
    latency_ms: int,
    event: str,
    extra: Optional[dict] = None,
) -> None:
    row: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "provider": provider,
        "style": style,
        "audience": audience,
        "success": success,
        "latency_ms": latency_ms,
        "event": event,
    }
    if extra:
        row.update(extra)
    _audit_logger.info(json.dumps(row))


def log_chart_audit_trail(
    session_id: str,
    provider: str,
    style_key: str,
    audience: str,
    entry: dict[str, Any],
) -> None:
    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "provider": provider,
        "style": style_key,
        "audience": audience,
        "success": True,
        "latency_ms": 0,
        "event": "chart_audit_trail",
        "chart_audit": entry,
    }
    _audit_logger.info(json.dumps(row))
