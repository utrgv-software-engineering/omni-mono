"""Tools that always need a person's approval.

``security.human_approval_tools`` in a project's ``project.yml`` names them, so
changing the list is a reviewed change to the project. Each entry is a tool
name, ``re:<pattern>`` (matched against the tool name), or an MCP
``server/tool`` (``server/*`` for all of a server's tools).

For a named tool the approval is always a person's: autopilot's allow-all
patterns, "always allow", ``skip_approvals``, the guardian reviewer and
contained-sandbox auto-approval never cover it.

A call a person approved after the run waiting on it ended (a restart) is
re-driven in a new turn; ``preapprove`` lets that exact call (tool and
arguments) through once.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Optional, Tuple

__all__ = [
    "arguments_digest",
    "consume_preapproval",
    "preapprove",
    "requires_person",
    "session_entries",
]

_preapproved: set[str] = set()


def requires_person(
    entries: Optional[Iterable[str]],
    tool_name: Optional[str],
    mcp_identity: Optional[Tuple[str, str]] = None,
) -> bool:
    for entry in entries or ():
        entry = str(entry).strip()
        if not entry:
            continue
        if entry.startswith("re:"):
            try:
                if tool_name and re.fullmatch(entry[3:], tool_name):
                    return True
            except re.error:
                continue
        elif "/" in entry:
            server, _, tool = entry.partition("/")
            if (
                mcp_identity
                and mcp_identity[0] == server
                and tool in ("*", mcp_identity[1])
            ):
                return True
        elif entry == tool_name:
            return True
    return False


def session_entries(session: Any) -> list:
    return list(getattr(session, "human_approval_tools", None) or [])


def arguments_digest(tool_name: str, arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    text = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(f"{tool_name}\n{text}".encode()).hexdigest()


def preapprove(session_id: str, tool_name: str, arguments: Any) -> None:
    _preapproved.add(f"{session_id}:{arguments_digest(tool_name, arguments)}")


def consume_preapproval(session_id: str, tool_name: str, arguments: Any) -> bool:
    key = f"{session_id}:{arguments_digest(tool_name, arguments)}"
    if key in _preapproved:
        _preapproved.discard(key)
        return True
    return False
