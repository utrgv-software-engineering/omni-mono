"""Structured user elicitation — the non-security input lane.

Agents and tools sometimes need a *decision or piece of information* from
the user that is not a security approval: a short question, a filled-in
form, a confirmation, a selection between options, or an action completed
at a URL. ``AgentService.request_elicitation`` emits an
``elicitation_requested`` notification, awaits the first
``elicitation_response`` from any connected tab, and resolves exactly
once. This module holds the pure contract pieces: the request kinds, the
typed RPC errors, and response-value validation.

Boundary with approvals
-----------------------

Tool/MCP approvals are a *security* surface: their prompts gate whether
the agent may execute something, their responses feed the audit trail,
and their always-allow semantics mutate session policy. Elicitation is a
parallel lane for ordinary input; it deliberately reuses the same
*plumbing* (correlation ids, journaled replay, first-response-wins,
run-scoped clearing) without routing through — or being routable to —
the approval RPCs. Nothing answered through an elicitation can approve a
tool call.

Response persistence
--------------------

Each request carries ``persist_response`` (default ``False``). Only when
it is ``True`` does the submitted value appear in the journaled
``elicitation_resolved`` event (and therefore in replayed conversation
history). When it is ``False`` the resolution event carries the status
and action only — the answer value stays with the in-process caller and
never enters durable history. Callers that request a sensitive value
must also refrain from echoing it into model-visible context.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

__all__ = [
    "ELICITATION_KINDS",
    "TERMINAL_ELICITATION_STATUSES",
    "ElicitationError",
    "ElicitationNotFound",
    "ElicitationAlreadyResolved",
    "ElicitationInvalidResponse",
    "validate_elicitation_request",
    "validate_response_value",
]

#: Supported request kinds. ``question`` (free text), ``confirm``
#: (yes/no), ``select`` (choose among options), ``form`` (structured
#: fields described by ``input_schema``), ``url`` (complete an action at
#: an external URL). Clients that cannot render a kind or schema decline
#: with a typed ``decline`` response instead of crashing.
ELICITATION_KINDS = frozenset({"question", "confirm", "select", "form", "url"})

#: Response actions a client may take.
RESPONSE_ACTIONS = frozenset({"accept", "decline", "cancel"})

#: Statuses an ``elicitation_resolved`` event may carry.
TERMINAL_ELICITATION_STATUSES = frozenset(
    {"accepted", "declined", "cancelled", "expired", "server_restart"}
)


class ElicitationError(Exception):
    """Base class for typed elicitation RPC errors.

    Instances carry ``code`` and ``data`` so the JSON-RPC endpoint can
    surface them structurally (same contract as ``ResyncRequiredError``
    and ``AccountRpcError``).
    """

    code: int = -32050
    kind: str = "elicitation_error"

    def __init__(self, message: str, *, elicitation_id: str) -> None:
        super().__init__(message)
        self.elicitation_id = elicitation_id

    @property
    def data(self) -> Dict[str, Any]:
        return {"kind": self.kind, "elicitation_id": self.elicitation_id}


class ElicitationNotFound(ElicitationError):
    """The named elicitation does not exist (never issued, or resolved so
    long ago its record was evicted)."""

    code = -32050
    kind = "elicitation_not_found"

    def __init__(self, elicitation_id: str) -> None:
        super().__init__("Elicitation not found", elicitation_id=elicitation_id)


class ElicitationAlreadyResolved(ElicitationError):
    """A response arrived for an elicitation that is already resolved.

    First response wins; every later response — another tab, a retry, a
    response after expiry/cancellation — gets this predictable rejection
    with the winning ``status``.
    """

    code = -32051
    kind = "elicitation_already_resolved"

    def __init__(self, elicitation_id: str, *, status: str) -> None:
        super().__init__("Elicitation already resolved", elicitation_id=elicitation_id)
        self.status = status

    @property
    def data(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "elicitation_id": self.elicitation_id,
            "status": self.status,
        }


class ElicitationInvalidResponse(ElicitationError):
    """The response does not satisfy the request's contract (bad action,
    missing value, or value failing the validation schema)."""

    code = -32052
    kind = "elicitation_invalid_response"

    def __init__(self, elicitation_id: str, errors: List[str]) -> None:
        super().__init__("Elicitation response invalid", elicitation_id=elicitation_id)
        self.errors = list(errors)

    @property
    def data(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "elicitation_id": self.elicitation_id,
            "errors": list(self.errors),
        }


def validate_elicitation_request(
    kind: str,
    *,
    options: Optional[List[Any]] = None,
    url: Optional[str] = None,
) -> None:
    """Validate a request at creation time (server-side caller bug guard).

    Raises ``ValueError`` — requests are minted by in-process callers, so
    a bad one is a programming error, not a wire error.
    """
    if kind not in ELICITATION_KINDS:
        raise ValueError(
            f"Unknown elicitation kind {kind!r}; "
            f"expected one of {sorted(ELICITATION_KINDS)}"
        )
    if kind == "select" and not options:
        raise ValueError("select elicitations require a non-empty options list")
    if kind == "url" and not url:
        raise ValueError("url elicitations require a url")


def _option_values(options: List[Any]) -> Optional[List[Any]]:
    """The selectable values of an options list, or ``None`` when the
    entries carry no comparable ``value`` (open-record options skip the
    subset check)."""
    values = []
    for option in options:
        if isinstance(option, Mapping) and "value" in option:
            values.append(option["value"])
        elif isinstance(option, (str, int, float, bool)):
            values.append(option)
        else:
            return None
    return values


def validate_response_value(
    request: Mapping[str, Any],
    action: str,
    value: Optional[Mapping[str, Any]],
) -> List[str]:
    """Validate one response against its request contract.

    Returns a list of human-readable problems (empty means valid).
    ``decline`` and ``cancel`` never require a value. ``accept`` value
    conventions per kind:

    * ``question`` — ``{"text": <str>}``
    * ``confirm``  — ``{"confirmed": <bool>}``
    * ``select``   — ``{"selected": [<option value>, ...]}``
    * ``form``     — object validated against ``input_schema`` when the
      ``jsonschema`` package is available (structural dict check
      otherwise)
    * ``url``      — optional acknowledgement object
    """
    errors: List[str] = []
    if action not in RESPONSE_ACTIONS:
        errors.append(
            f"action must be one of {sorted(RESPONSE_ACTIONS)}, got {action!r}"
        )
        return errors
    if action != "accept":
        return errors

    kind = request.get("kind")
    if kind == "question":
        text = (value or {}).get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append("question responses require value.text (non-empty string)")
    elif kind == "confirm":
        confirmed = (value or {}).get("confirmed")
        if not isinstance(confirmed, bool):
            errors.append("confirm responses require value.confirmed (boolean)")
    elif kind == "select":
        selected = (value or {}).get("selected")
        if not isinstance(selected, list) or not selected:
            errors.append("select responses require value.selected (non-empty list)")
        else:
            allowed = _option_values(list(request.get("options") or []))
            if allowed is not None:
                unknown = [item for item in selected if item not in allowed]
                if unknown:
                    errors.append(
                        f"selected values not offered by the request: {unknown!r}"
                    )
    elif kind == "form":
        if not isinstance(value, Mapping):
            errors.append("form responses require an object value")
        else:
            schema = request.get("input_schema")
            if isinstance(schema, Mapping) and schema:
                errors.extend(_validate_against_schema(dict(schema), dict(value)))
    # ``url`` accepts an optional acknowledgement object; nothing to check.
    return errors


def _validate_against_schema(
    schema: Dict[str, Any], value: Dict[str, Any]
) -> List[str]:
    """JSON-Schema validation when ``jsonschema`` is importable.

    The server extra ships ``jsonschema`` transitively; bare installs
    fall back to accepting any object (clients still validate locally).
    """
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - environment without jsonschema
        return []
    try:
        validator = Draft202012Validator(schema)
    except Exception:
        return ["request carries an invalid validation schema"]
    return [
        f"value{'.' + '.'.join(str(p) for p in error.path) if error.path else ''}: "
        f"{error.message}"
        for error in validator.iter_errors(value)
    ]
