"""What the deployment requires: one place every rule reads.

``WORKPLACE_POLICY`` is set by whoever deploys Workplace, not by a factory:

* ``open`` (the default): local installations and development.
* ``strict``: deployments that hold sensitive data, and the 80/20 deployment
  that dogfoods them. Sessions end after 30 minutes without activity and 12
  hours in total; with Azure Easy Auth, password sign-in is off; factory
  runtimes keep an audit of who did what, without conversation content.

Later rules (classification, allowed services, forced review) read
``Policy.strict`` here rather than flags of their own.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

import yaml

OPEN = "open"
STRICT = "strict"

#: Strict sessions: 12 hours in total, 30 minutes without activity (UTS 165).
STRICT_SESSION_SECONDS = 12 * 3600
STRICT_IDLE_SECONDS = 30 * 60

#: Managed overlay for factory runtimes under the strict policy: the runtime's
#: own audit, with no tool input or output in it.
RUNTIME_OVERLAY = {
    "security": {"audit": {"enabled": True, "backend": "file", "max_detail_chars": 0}}
}


class Policy:
    def __init__(self, environ: Optional[Mapping[str, str]] = None) -> None:
        env = os.environ if environ is None else environ
        value = env.get("WORKPLACE_POLICY", OPEN).strip().lower() or OPEN
        if value not in {OPEN, STRICT}:
            raise ValueError("WORKPLACE_POLICY must be open or strict")
        self.name = value

    @property
    def strict(self) -> bool:
        return self.name == STRICT

    def session_limits(self) -> dict:
        """``Accounts`` keyword arguments for this policy's sessions."""
        if not self.strict:
            return {}
        return {
            "session_seconds": STRICT_SESSION_SECONDS,
            "idle_seconds": STRICT_IDLE_SECONDS,
        }

    def runtime_environment(
        self, directory: Path, security: Optional[dict] = None
    ) -> dict:
        """Environment for a factory runtime: under the strict policy, a managed
        configuration overlay the factory's project cannot override, with any
        further ``security`` settings (the MCP allowlist) in it."""
        if not self.strict:
            return {}
        overlay = {"security": {**RUNTIME_OVERLAY["security"], **(security or {})}}
        path = Path(directory) / "policy-overlay.yml"
        path.write_text(yaml.safe_dump(overlay, sort_keys=False))
        path.chmod(0o600)
        return {"OMNIAGENTS_PROJECT_OVERLAYS": str(path)}
