"""Builtin tools.

Exports are resolved lazily. Importing this package used to import every
tool module eagerly, which meant one unavailable dependency took the
whole package down: in a constrained runtime (Pyodide/WebAssembly, a slim
container) ``from omniagents.builtin.tools import read_file`` failed
because ``worker_tools`` pulls in the RPC server, which pulls in FastAPI,
which pulls in ``agents.mcp`` — none of which the file tools need.

Now a name is imported when it is first accessed, so each tool family
costs only its own dependencies.

This does NOT change tool registration: the loader discovers builtin tools
by scanning this directory and importing each module separately
(``discover_functions_in_dir``), which already skips modules that fail to
import and warns. Lazy exports simply make direct imports behave the same
way.

Unlike the lazy loader in the top-level ``omniagents`` package, a failed
import here is raised rather than turned into ``None`` — a tool that
silently becomes ``None`` fails later and far more confusingly than one
that fails at the import that asked for it.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List

# Exported name -> the module in this package that defines it
_EXPORTS: Dict[str, str] = {
    "display_artifact": "artifacts",
    "execute_bash": "bash_tool",
    "bash_status": "bash_tool",
    "bash_logs": "bash_tool",
    "bash_kill": "bash_tool",
    "convert_to_markdown": "convert_to_markdown",
    "download_file": "download_file",
    "list_directory": "file_tools",
    "read_file": "file_tools",
    "edit_file": "file_tools",
    "write_file": "file_tools",
    "apply_patch": "file_tools",
    "ask_user": "elicit",
    "goal_complete": "goal_tools",
    "notify": "human",
    "escalate": "human",
    "read_image": "image_tools",
    "loop_continue": "loop_tools",
    "read_notebook": "notebook_tools",
    "edit_notebook": "notebook_tools",
    "scholar_search": "scholar_search",
    "load_skill": "skill_tool",
    "dispatch": "slash_tool",
    "task_create": "task_list_tools",
    "task_get": "task_list_tools",
    "task_list": "task_list_tools",
    "task_update": "task_list_tools",
    "schedule_wakeup": "wakeup_tools",
    "cancel_wakeup": "wakeup_tools",
    "web_fetch": "web_fetch",
    "web_search": "web_search",
    "youtube_search": "youtube_search",
    "spawn_worker": "worker_tools",
    "wait_for_workers": "worker_tools",
    "get_worker_status": "worker_tools",
    "send_worker_message": "worker_tools",
    "close_worker": "worker_tools",
    "resume_worker": "worker_tools",
    "cleanup_worker_worktree": "worker_tools",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache so later lookups skip __getattr__
    return value


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    "display_artifact",
    "execute_bash",
    "bash_status",
    "bash_logs",
    "bash_kill",
    "convert_to_markdown",
    "download_file",
    "list_directory",
    "read_file",
    "edit_file",
    "write_file",
    "apply_patch",
    "goal_complete",
    "loop_continue",
    "read_notebook",
    "edit_notebook",
    "dispatch",
    "schedule_wakeup",
    "cancel_wakeup",
    "notify",
    "escalate",
    "ask_user",
    "read_image",
    "scholar_search",
    "load_skill",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "web_fetch",
    "web_search",
    "youtube_search",
    "spawn_worker",
    "wait_for_workers",
    "get_worker_status",
    "send_worker_message",
    "close_worker",
    "resume_worker",
    "cleanup_worker_worktree",
]
