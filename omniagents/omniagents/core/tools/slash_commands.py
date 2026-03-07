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
        return self._normalize(name) in self._commands

    async def execute(self, name: str, arg: str | None = None) -> str:
        normalized = self._normalize(name)
        handler = self._commands.get(normalized)
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
    def _normalize(name: str) -> str:
        return (name or "").strip().lower().lstrip("/")
