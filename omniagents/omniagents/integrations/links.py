"""Which pull requests a run produced, or worked on: what a PR's cost and history add up from.

When a run ends, its pull requests go in the event store's
``run_pull_requests``:

- ``opened``: one its sessions (its own, and those of the agents it called as
  tools) created, read from their ``create_pull_request`` results (GitHub's
  MCP server answers with the pull request's URL);
- ``worked``: the one whose event started it (a review requested, a check
  failed, a comment on it): its thread is the pull request.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .events import IncomingEvent
from .store import EventStore

__all__ = ["PullRequestLinks", "opened_pull_requests"]

logger = logging.getLogger(__name__)

_PULL_URL = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)")
_THREAD = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)$")


def _text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):  # [{'type': 'input_text', 'text': ...}]
        return " ".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in output
        )
    return json.dumps(output, default=str) if output is not None else ""


def opened_pull_requests(history: Iterable[Dict[str, Any]]) -> List[Tuple[str, int]]:
    """``(owner/repo, number)`` of each pull request a session's ``create_pull_request``
    calls opened."""
    calls = {
        item.get("call_id")
        for item in history
        if item.get("type") == "function_call"
        and str(item.get("name") or "").endswith("create_pull_request")
    }
    found: List[Tuple[str, int]] = []
    for item in history:
        if (
            item.get("type") != "function_call_output"
            or item.get("call_id") not in calls
        ):
            continue
        match = _PULL_URL.search(_text(item.get("output")))
        if match:
            found.append((f"{match.group(1)}/{match.group(2)}", int(match.group(3))))
    return list(dict.fromkeys(found))


class PullRequestLinks:
    def __init__(
        self,
        store: EventStore,
        history: Callable[[str, str], List[Dict[str, Any]]],
        children: Callable[[str, str], List[Tuple[str, str, List[Dict[str, Any]]]]],
    ) -> None:
        self.store = store
        self.history = history
        self.children = children

    def record(
        self, dispatch_id: int, agent: str, session_id: str, event: IncomingEvent
    ) -> None:
        about_a_pull = event.event.startswith("pull_request") or isinstance(
            event.payload.get("pull_request"), dict
        )
        if event.provider == "github" and about_a_pull:
            match = _THREAD.match(event.thread_key or "")
            if match:
                self.store.link_pull_request(
                    dispatch_id, match.group(1), int(match.group(2)), "worked"
                )
        histories: List[List[Dict[str, Any]]] = []
        try:
            scoped = self.store.run_history(dispatch_id)
            histories.append(
                scoped if scoped is not None else self.history(agent, session_id)
            )
            if scoped is None:
                histories += [
                    history for _, _, history in self.children(agent, session_id)
                ]
        except Exception:
            logger.exception(
                "integrations: reading run %s's sessions for its pull requests failed",
                dispatch_id,
            )
        for history in histories:
            for repository, number in opened_pull_requests(history):
                self.store.link_pull_request(dispatch_id, repository, number, "opened")
