from __future__ import annotations

"""OmniAgents server package.

This module provides a minimal JSON-RPC/WebSocket server that exposes an
Agent built from the same YAML spec the GUI/CLI modes use.  Remote
clients can start/stop runs and receive stream events in real time.

The implementation purposefully keeps *all* heavy lifting (Agent
building, SafeAgent wrapping, Runner invocation…) in the existing core
code.  The server layer is only responsible for:

1. Managing per-connection Session objects.
2. Translating Runner.stream_events() into JSON-serialisable payloads
   pushed over fastapi-websocket-rpc notifications.
3. Exposing a very small RPC surface (start_run, stop_run, send_user_message).
"""

from .sessions import Session, SessionManager  # re-export for convenience 