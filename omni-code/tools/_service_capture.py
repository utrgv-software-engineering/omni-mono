"""Process-wide capture of the running AgentService.

Tools (background bash spawn, worker on_complete callback) need to
call ``service.enqueue_notification`` but don't get a service handle
through ``ctx``. Every server-function on_invoke handler in this
project calls ``set_service(service)`` first thing — by the time the
first background job exits or the first worker completes, the capture
has fired.

Returns ``None`` for the brief window before any server_function has
been invoked. Callers MUST handle that gracefully (drop the
notification, debug-log).
"""

from __future__ import annotations

import sys
from typing import Any, Optional

# Stash on sys (not module global) for the same reason _bash_jobs does:
# omniagents.core.tools.discovery wipes sys.modules entries after
# loading tools/, which would otherwise create a second capture slot.
_ATTR = "__omni_code_agent_service__"


def set_service(service: Any) -> None:
    setattr(sys.modules["sys"], _ATTR, service)


def get_service() -> Optional[Any]:
    # Prefer the omniagents-side global — it's set unconditionally in
    # AgentService.__init__ so it's available the moment the server
    # comes up, well before any tool can fire. Fall back to whatever
    # _ensure_sweeper captured (legacy path, kept so this still works
    # if omniagents predates current_service()).
    try:
        from omniagents.core.agents.service import current_service
        svc = current_service()
        if svc is not None:
            return svc
    except Exception:
        pass
    return getattr(sys.modules["sys"], _ATTR, None)
