"""Slash commands: the typed, user-facing subset of server functions.

A server function is an RPC: a name, a JSON schema, a scope. A slash
command is something a person types. They are different things, so a
function is a slash command only when it says so::

    @slash_command("goal", description="Start an autonomous goal loop",
                   usage="<goal text>", args=text("goal"), during_run=False)
    @server_function(name_override="goal", ...)
    async def goal(...): ...

Products bind a slash name to a project agent in their manifest's
``slash_commands:`` block instead; ``AgentService`` turns each binding
into a server function carrying the same declaration.

Clients read the catalog through ``server_call("slash.list")``, render
only that, and turn a typed line into the call's arguments with the
declared rule. There are three rules and none of them is JSON:

- ``none``: the command takes no arguments; trailing text is refused.
- ``text(field)``: everything after the name goes into one field.
- ``words(a, b, ..., rest)``: whitespace-separated positional tokens;
  the last field takes the remainder of the line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "SlashArgs",
    "SlashDeclaration",
    "none",
    "text",
    "words",
    "slash_command",
    "slash_declaration_of",
    "parse_slash_args",
]


@dataclass(frozen=True)
class SlashArgs:
    kind: str  # "none" | "text" | "words"
    fields: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.kind == "text":
            out["field"] = self.fields[0]
        elif self.kind == "words":
            out["fields"] = list(self.fields)
        return out


def none() -> SlashArgs:
    return SlashArgs("none")


def text(field_name: str = "text") -> SlashArgs:
    return SlashArgs("text", (field_name,))


def words(*field_names: str) -> SlashArgs:
    if not field_names:
        raise ValueError("words() needs at least one field")
    return SlashArgs("words", tuple(field_names))


@dataclass(frozen=True)
class SlashDeclaration:
    name: str
    description: str
    usage: str = ""
    args: SlashArgs = field(default_factory=none)
    aliases: tuple[str, ...] = ()
    #: False when the command must not run while a run is active
    #: (it mutates session state the run depends on).
    during_run: bool = True
    #: Presentation order in popups; lower first, ties by name.
    order: int = 100

    def to_dict(self, function: str) -> Dict[str, Any]:
        return {
            "name": self.name,
            "function": function,
            "description": self.description,
            "usage": self.usage,
            "args": self.args.to_dict(),
            "aliases": list(self.aliases),
            "during_run": self.during_run,
            "order": self.order,
        }


_ATTR = "_slash_command"


def slash_command(
    name: Optional[str] = None,
    *,
    description: str,
    usage: str = "",
    args: Optional[SlashArgs] = None,
    aliases: Sequence[str] = (),
    during_run: bool = True,
    order: int = 100,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a server function as typeable. Works above or below
    ``@server_function``; the name defaults to the function's name."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        resolved = (
            name
            or getattr(func, "_server_function_name", None)
            or getattr(func, "__name__", None)
        )
        if not resolved:
            raise ValueError("slash_command needs a name")
        setattr(
            func,
            _ATTR,
            SlashDeclaration(
                name=str(resolved).strip().lstrip("/").lower(),
                description=description,
                usage=usage,
                args=args or none(),
                aliases=tuple(a.strip().lstrip("/").lower() for a in aliases),
                during_run=during_run,
                order=order,
            ),
        )
        return func

    return decorator


def slash_declaration_of(func: Any) -> Optional[SlashDeclaration]:
    value = getattr(func, _ATTR, None)
    return value if isinstance(value, SlashDeclaration) else None


class SlashArgsError(ValueError):
    """The typed text does not fit the command's argument rule."""


def parse_slash_args(rule: Dict[str, Any] | SlashArgs, rest: str) -> Dict[str, Any]:
    """Turn the text after the command name into call arguments.

    Shared contract with the clients (each ships the same rule in its own
    language); the server applies it too so a caller that sends
    ``{"text": ...}`` for a ``words`` command still gets the right shape.
    """
    spec = rule.to_dict() if isinstance(rule, SlashArgs) else dict(rule)
    kind = spec.get("kind", "none")
    rest = (rest or "").strip()
    if kind == "none":
        if rest:
            raise SlashArgsError("This command takes no arguments.")
        return {}
    if kind == "text":
        field_name = str(spec.get("field") or "text")
        return {field_name: rest} if rest else {}
    if kind == "words":
        fields: List[str] = list(spec.get("fields") or [])
        if not fields:
            return {}
        parts = rest.split(None, len(fields) - 1) if rest else []
        return {name: value for name, value in zip(fields, parts)}
    raise SlashArgsError(f"Unknown argument rule {kind!r}.")
