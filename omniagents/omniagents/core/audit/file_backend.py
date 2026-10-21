"""File-based audit backend — writes JSON lines to a local file.

Activated when ``security.audit.enabled: true`` and
``security.audit.backend: file`` in ``project.yml``.

The backend is wired as a runtime hook listening for ``"audit"`` events
emitted by the service, bridge, and SafeAgent layers.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Optional


class FileAuditBackend:
    """Append-only JSON-lines audit log with optional HMAC chain."""

    def __init__(
        self,
        path: str,
        retention_days: int = 0,
        signing_key: Optional[str] = None,
    ) -> None:
        self._path = Path(path)
        self._retention_days = retention_days
        self._signing_key = signing_key
        self._lock = Lock()
        self._ensure_dir()
        self._purge_if_needed()
        # Initialise HMAC chain state
        self._prev_hmac: Optional[str]
        if self._signing_key:
            from omniagents.core.audit.integrity import CHAIN_SEED

            self._prev_hmac = CHAIN_SEED
            self._recover_chain_tail()
        else:
            self._prev_hmac = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def handle_event(
        self, *, service: Any = None, event: str = "", **kwargs: Any
    ) -> None:
        """Runtime-hook entry point — called for every ``"audit"`` event."""
        line = self._serialise(kwargs)
        if line is None:
            return
        if self._signing_key:
            line = self._sign_entry(line)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sign_entry(self, line: str) -> str:
        """Add HMAC to a serialised audit entry and advance the chain."""
        from omniagents.core.audit.integrity import HMAC_FIELD, compute_hmac

        # Use canonical (sorted-keys) serialization so verify_audit_log
        # produces the same bytes when it strips _hmac and re-serializes.
        obj = json.loads(line)
        canonical = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
        # _sign_entry is only reached when signing is enabled (see append()).
        assert self._prev_hmac is not None and self._signing_key is not None
        h = compute_hmac(canonical, self._prev_hmac, self._signing_key)
        obj[HMAC_FIELD] = h
        self._prev_hmac = h
        return json.dumps(obj, default=str, ensure_ascii=False)

    def _recover_chain_tail(self) -> None:
        """Read the last HMAC from an existing log to resume the chain."""
        if not self._path.exists():
            return
        try:
            last_hmac = None
            with open(self._path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        from omniagents.core.audit.integrity import HMAC_FIELD

                        obj = json.loads(raw)
                        h = obj.get(HMAC_FIELD)
                        if h is not None:
                            last_hmac = h
                    except (json.JSONDecodeError, TypeError):
                        pass
            if last_hmac is not None:
                self._prev_hmac = last_hmac
        except Exception:
            pass  # best-effort; start fresh if recovery fails

    def _ensure_dir(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _serialise(self, data: Dict[str, Any]) -> Optional[str]:
        try:
            return json.dumps(data, default=str, ensure_ascii=False)
        except Exception:
            return None

    def _purge_if_needed(self) -> None:
        """Remove entries older than *retention_days* on startup."""
        if self._retention_days <= 0 or not self._path.exists():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        cutoff_iso = cutoff.isoformat()
        try:
            kept: list[str] = []
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        ts = obj.get("timestamp", "")
                        if ts >= cutoff_iso:
                            kept.append(line)
                    except (json.JSONDecodeError, TypeError):
                        kept.append(line)  # keep unparseable lines
            with open(self._path, "w", encoding="utf-8") as fh:
                for line in kept:
                    fh.write(line + "\n")
        except Exception:
            pass  # best-effort


def _default_audit_path(project_slug: str | None) -> str:
    """Return ``~/.omniagents/audit/{project}.jsonl``."""
    base = Path.home() / ".omniagents" / "audit"
    slug = project_slug or "default"
    return str(base / f"{slug}.jsonl")


def create_audit_hook(
    audit_config: Dict[str, Any],
    project_slug: str | None = None,
    secrets_provider: Any | None = None,
) -> Callable[..., Any]:
    """Create and return the runtime-hook callable for the file backend.

    Called once during :class:`AgentService` initialisation.

    If a *secrets_provider* is given and the audit config specifies an
    ``hmac_secret_key`` (or defaults to ``OMNIAGENTS_AUDIT_HMAC_KEY``),
    the backend will sign each entry with an HMAC chain for tamper
    detection.
    """
    path = audit_config.get("path") or _default_audit_path(project_slug)
    retention_days = int(audit_config.get("retention_days", 0))
    signing_key = None
    if secrets_provider is not None:
        key_name = audit_config.get("hmac_secret_key", "OMNIAGENTS_AUDIT_HMAC_KEY")
        signing_key = secrets_provider.get_secret(key_name)
    backend = FileAuditBackend(
        path=path, retention_days=retention_days, signing_key=signing_key
    )
    return backend.handle_event
