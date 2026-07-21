from __future__ import annotations

import inspect
from typing import Any, Callable

from .discovery import RichToolOutput


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, handler: Callable[..., Any]) -> None:
        normalized = self._normalize(name)
        self._commands[normalized] = handler

    def has(self, name: str) -> bool:
        normalized = self._normalize(name)
        if normalized in self._commands:
            return True
        return self._manifest_handler(normalized) is not None

    async def execute(self, name: str, arg: str | None = None) -> str:
        normalized = self._normalize(name)
        handler = self._commands.get(normalized)
        if handler is None:
            handler = self._manifest_handler(normalized)
        if handler is None:
            return f"Unknown command: {name}"

        try:
            result = handler(arg)
            if inspect.isawaitable(result):
                result = await result
        except TypeError:
            result = handler()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return f"Failed to run /{normalized}: {exc}"

        if result is None:
            return ""
        return str(result)

    def create_rich_output(
        self, *, command: str, arg: str | None, output: str
    ) -> RichToolOutput:
        is_error = output.startswith("Unknown command:") or output.startswith(
            "Failed to run /"
        )
        preview = output[:1500] if output else ""
        truncated = len(output) > 1500 if output else False
        summary = f"/{self._normalize(command)}"
        if arg:
            summary += f" {arg}"
        if is_error:
            summary += " failed"
        return RichToolOutput(
            output,
            {
                "display_type": "error" if is_error else "command",
                "summary": summary,
                "preview": preview,
                "truncated": truncated,
                "metadata": {
                    "success": not is_error,
                    "command": self._normalize(command),
                    "output_length": len(output) if output else 0,
                },
            },
        )

    @staticmethod
    def _manifest_handler(normalized: str):
        """Fallback: the installed product manifest's declarative
        ``slash_commands:`` binding for *normalized*, wrapped as a
        handler (see ``omniagents.core.runtime.slash_agents``). A
        Python-registered handler of the same name always wins; a broken
        manifest degrades to "Unknown command"."""
        try:
            from omniagents.core.runtime.slash_agents import (
                manifest_slash_handler,
            )

            return manifest_slash_handler(normalized)
        except Exception:
            return None

    @staticmethod
    def _normalize(name: str) -> str:
        return (name or "").strip().lower().lstrip("/")


# ---------------------------------------------------------------------------
# Process-wide default registry
#
# The builtin `dispatch` tool (slash capability pack) executes commands from
# this registry. Products register their commands at import time — typically
# from the module that installs their other process-wide seams (prompt
# builders, skill roots) — so registration precedes any agent run.
# ---------------------------------------------------------------------------

_default_registry = SlashCommandRegistry()


def default_slash_registry() -> SlashCommandRegistry:
    """The registry the builtin ``dispatch`` tool executes against."""
    return _default_registry


def register_slash_command(name: str, handler: Callable[..., Any]) -> None:
    """Register *handler* as ``/<name>`` on the default registry.

    The handler may be sync or async and may take an optional ``arg``
    string (the text after the command name). Re-registering a name
    replaces the previous handler.
    """
    _default_registry.register(name, handler)
