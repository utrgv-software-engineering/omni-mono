"""Remote audit backend — ships events to the omni-platform management plane.

Replaces (or supplements) the local file backend. Events are buffered in
memory and flushed in batches to ``POST /api/v1/audit/ingest``.

Activated when ``security.audit.backend: "remote"`` in ``project.yml``::

    security:
      audit:
        enabled: true
        backend: "remote"
        platform_url: "${OMNI_PLATFORM_URL}"
        flush_interval: 5         # seconds, default
        flush_batch_size: 50      # events per batch, default
        fallback_path: ""         # optional local file fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("omniagents.audit.remote")


def _expand_env(value: str) -> str:
    if not value:
        return value
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        inner = value[2:-1]
        if ":-" in inner:
            var, default = inner.split(":-", 1)
            return os.getenv(var, default)
        return os.getenv(inner, "")
    return value


class RemoteAuditBackend:
    """Buffers audit events and flushes them to the management plane API.

    Events are assigned a client-side ``event_id`` for idempotent ingestion.
    If the API is unreachable, events are written to a local fallback file
    (if configured) for later replay.
    """

    def __init__(
        self,
        platform_url: str,
        runtime_token: str = "",
        session_id: str = "",
        flush_interval: float = 5.0,
        flush_batch_size: int = 50,
        fallback_path: str = "",
    ) -> None:
        self._platform_url = platform_url.rstrip("/")
        self._ingest_url = f"{self._platform_url}/api/v1/audit/ingest"
        self._runtime_token = runtime_token or os.getenv("OMNI_RUNTIME_TOKEN", "")
        self._session_id = session_id or os.getenv("OMNI_SESSION_ID", "")
        self._flush_interval = flush_interval
        self._flush_batch_size = flush_batch_size
        self._fallback_path = fallback_path

        self._buffer: Deque[Dict[str, Any]] = deque()
        self._lock = Lock()
        self._last_flush = time.monotonic()
        self._flush_task: Optional[asyncio.Task] = None

    async def handle_event(
        self, *, service: Any = None, event: str = "", **kwargs: Any
    ) -> None:
        """Runtime-hook entry point — called for every ``"audit"`` event."""
        audit_event = self._to_ingest_format(kwargs)
        with self._lock:
            self._buffer.append(audit_event)

        # Flush if batch is full or interval has elapsed
        should_flush = (
            len(self._buffer) >= self._flush_batch_size
            or (time.monotonic() - self._last_flush) >= self._flush_interval
        )
        if should_flush:
            await self._flush()

    async def flush_remaining(self) -> None:
        """Flush any remaining buffered events. Call on shutdown."""
        await self._flush()

    async def _flush(self) -> None:
        """Send buffered events to the management plane."""
        with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()
            self._last_flush = time.monotonic()

        try:
            import requests

            response = await asyncio.to_thread(
                requests.post,
                self._ingest_url,
                json={
                    "events": batch,
                    "session_id": self._session_id,
                },
                headers={
                    "Authorization": f"Bearer {self._runtime_token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )

            if response.status_code == 401:
                logger.warning(
                    "Audit ingest returned 401 — runtime token may be expired"
                )
                self._write_fallback(batch)
            elif not response.ok:
                logger.warning(
                    "Audit ingest returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                self._write_fallback(batch)
            else:
                result = response.json()
                if result.get("rejected", 0) > 0:
                    logger.warning(
                        "Audit ingest rejected %d events: %s",
                        result["rejected"],
                        result.get("errors", []),
                    )
        except Exception as exc:
            logger.warning("Audit ingest failed: %s", exc)
            self._write_fallback(batch)

    def _to_ingest_format(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Convert an omniagents audit event to the management plane ingest format."""
        event_type = kwargs.get("event_type", "unknown")
        details = dict(kwargs.get("details", {}))
        details["event_type"] = event_type

        # Map omniagents event fields to management plane schema
        return {
            "event_id": str(uuid.uuid4()),
            "action": self._map_action(event_type, kwargs),
            "resource_type": kwargs.get("resource_type"),
            "resource_id": kwargs.get("resource_id"),
            "agent_name": kwargs.get("agent_name"),
            "tool_name": details.get("tool_name"),
            "mcp_server": details.get("mcp_server"),
            "details": details,
            "severity": self._map_severity(event_type),
            "phi_involved": details.get("phi_involved", False),
        }

    @staticmethod
    def _map_action(event_type: str, kwargs: Dict[str, Any]) -> str:
        """Map omniagents event_type to a management plane action string."""
        mapping = {
            "run_started": "session.run_started",
            "run_end": "session.run_ended",
            "tool_called": "tool.invoked",
            "tool_result": "tool.completed",
            "tool_approved": "tool.approved",
            "tool_rejected": "tool.rejected",
            "guardrail_triggered": "guardrail.triggered",
            "incident": "incident.reported",
        }
        return mapping.get(event_type, f"agent.{event_type}")

    @staticmethod
    def _map_severity(event_type: str) -> str:
        if event_type in ("incident", "guardrail_triggered"):
            return "warn"
        if event_type == "tool_rejected":
            return "warn"
        return "info"

    def _write_fallback(self, batch: List[Dict[str, Any]]) -> None:
        """Write failed events to a local file for later replay."""
        if not self._fallback_path:
            return
        try:
            path = Path(self._fallback_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                for event in batch:
                    fh.write(json.dumps(event, default=str) + "\n")
        except Exception as exc:
            logger.warning("Fallback write failed: %s", exc)


def create_remote_audit_hook(
    audit_config: Dict[str, Any],
    project_slug: str | None = None,
    secrets_provider: Any | None = None,
) -> Callable[..., Any]:
    """Create and return the runtime-hook callable for the remote backend.

    Called once during :class:`AgentService` initialisation when
    ``audit.backend`` is ``"remote"``.
    """
    platform_url = _expand_env(audit_config.get("platform_url", "")) or _expand_env(
        "${OMNI_PLATFORM_URL}"
    )
    runtime_token = _expand_env(
        audit_config.get("runtime_token", "${OMNI_RUNTIME_TOKEN}")
    )
    session_id = _expand_env(audit_config.get("session_id", "${OMNI_SESSION_ID}"))

    backend = RemoteAuditBackend(
        platform_url=platform_url,
        runtime_token=runtime_token,
        session_id=session_id,
        flush_interval=float(audit_config.get("flush_interval", 5)),
        flush_batch_size=int(audit_config.get("flush_batch_size", 50)),
        fallback_path=audit_config.get("fallback_path", ""),
    )
    return backend.handle_event
