"""Local trace recording: the ``studio`` tracing provider.

Traces and spans from the Agents SDK land in per-project SQLite databases
under ``get_traces_dir()`` (``~/.omniagents/traces/<project>.db``).
Conversation feedback (``/good``, ``/bad``) is stored next to them as
analysis rows. OmniAgents Studio (the separate ``omniagents-studio``
package) is a viewer for this store; recording does not need it installed.
"""

from __future__ import annotations

import atexit
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.tracing import add_trace_processor, set_trace_processors
from agents.tracing import trace as sdk_trace

from omniagents.core.paths import get_traces_dir

from .processor import LocalTraceProcessor
from .storage import SQLiteStorage
from .utils import generate_readable_name

logger = logging.getLogger(__name__)

_processor: Optional[LocalTraceProcessor] = None
_current_name: Optional[str] = None
_default_project: Optional[str] = None
_cleanup_registered = False


def init(
    project: Optional[str] = "default",
    name: Optional[str] = None,
    db_path: Optional[str] = None,
    resume: str = "never",
    replace_default: bool = True,
) -> Optional[str]:
    """Start recording traces locally for ``project``.

    Returns the resolved trace name (``None`` when ``project`` is ``None``,
    which disables recording).
    """
    global _processor, _current_name, _default_project, _cleanup_registered

    _default_project = project

    if project is None:
        if replace_default:
            set_trace_processors([])
        return None

    if _processor:
        _processor.shutdown()
        _processor = None

    _processor = LocalTraceProcessor(default_project=_default_project)

    resolved_db_path: Optional[Path] = None
    if db_path:
        resolved_db_path = Path(db_path).expanduser().resolve()
    elif _default_project:
        resolved_db_path = get_traces_dir() / f"{_default_project}.db"

    if resolved_db_path is not None:
        _current_name = _resolve_trace_name(resolved_db_path, project, name, resume)

    if replace_default:
        set_trace_processors([_processor])
    else:
        add_trace_processor(_processor)

    if not _cleanup_registered:
        atexit.register(_cleanup)
        _cleanup_registered = True

    return _current_name


def _resolve_trace_name(
    db_path: Path, project: str, name: Optional[str], resume: str
) -> str:
    temp_storage = None
    try:
        if db_path.exists():
            temp_storage = SQLiteStorage(str(db_path))
            existing_traces = [t["workflow_name"] for t in temp_storage.get_traces()]
        else:
            existing_traces = []

        if resume == "must":
            if name is None:
                raise ValueError("Must provide a trace name when resume='must'")
            if name not in existing_traces:
                raise ValueError(
                    f"Trace '{name}' does not exist in project '{project}'"
                )
        elif resume == "never":
            if name is not None and name in existing_traces:
                for index in range(1000):
                    new_name = f"{name}-{index}"
                    if new_name not in existing_traces:
                        name = new_name
                        break
        elif resume != "allow":
            raise ValueError("resume must be one of: 'must', 'allow', or 'never'")

        return name if name is not None else generate_readable_name(existing_traces)
    finally:
        if temp_storage:
            temp_storage.close()


@contextmanager
def trace(
    workflow_name: str,
    trace_id: Optional[str] = None,
    group_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    disabled: bool = False,
    project: Optional[str] = None,
):
    """SDK ``trace()`` that routes the trace to a local project database."""
    target_project = project or _default_project

    if not target_project and not _processor:
        with sdk_trace(workflow_name, trace_id, group_id, metadata, disabled=True):
            yield
        return

    if not _processor and target_project:
        add_trace_processor(LocalTraceProcessor(default_project=None))

    enhanced_metadata = dict(metadata or {})
    if target_project:
        enhanced_metadata["studio_project"] = target_project

    with sdk_trace(
        workflow_name, trace_id, group_id, enhanced_metadata, disabled=disabled
    ):
        yield


def get_current_project() -> Optional[str]:
    return _default_project


def _analysis_storage(project: Optional[str]) -> tuple[str, SQLiteStorage]:
    target_project = project or _default_project or "default"
    return target_project, SQLiteStorage(str(get_traces_dir() / f"{target_project}.db"))


def set_conversation_analysis(
    group_id: str,
    *,
    judgment: Optional[str] = None,
    notes: Optional[str] = None,
    marked: bool = True,
    analyzed_at: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    target_project, storage = _analysis_storage(project)
    try:
        existing = storage.get_analysis(target_project, group_id) or {}
        payload = {
            "markedForAnalysis": bool(marked),
            "judgment": judgment if judgment is not None else existing.get("judgment"),
            "notes": notes if notes is not None else existing.get("notes", ""),
            "analyzedAt": analyzed_at
            or existing.get("analyzed_at")
            or datetime.now(timezone.utc).isoformat(),
        }
        storage.upsert_analysis(target_project, group_id, payload)
        return payload
    finally:
        storage.close()


def get_conversation_analysis(
    group_id: str,
    *,
    project: Optional[str] = None,
) -> dict:
    target_project, storage = _analysis_storage(project)
    try:
        return storage.get_analysis(target_project, group_id) or {}
    finally:
        storage.close()


def append_conversation_note(
    group_id: str,
    text: str,
    *,
    project: Optional[str] = None,
    separator: str = "\n",
) -> dict:
    target_project, storage = _analysis_storage(project)
    try:
        existing = storage.get_analysis(target_project, group_id) or {}
        base_notes = existing.get("notes", "") or ""
        payload = {
            "markedForAnalysis": existing.get("marked_for_analysis", False),
            "judgment": existing.get("judgment"),
            "notes": base_notes + (separator if base_notes else "") + text,
            "analyzedAt": datetime.now(timezone.utc).isoformat(),
        }
        storage.upsert_analysis(target_project, group_id, payload)
        return payload
    finally:
        storage.close()


def _cleanup() -> None:
    if _processor:
        try:
            _processor.shutdown()
        except Exception:
            pass


__all__ = [
    "init",
    "trace",
    "get_current_project",
    "set_conversation_analysis",
    "get_conversation_analysis",
    "append_conversation_note",
    "LocalTraceProcessor",
]
