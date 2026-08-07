"""Route resolution for tool/MCP approval requests.

Codex-style layering: approval strictness is a function of the execution
environment's actual blast radius plus the session's chosen reviewer.

* ``AUTO_APPROVE`` — the environment itself is the permission boundary: a
  sandboxed (non-host) environment, optionally required to lack the
  ``network`` capability. The call runs without any review; the decision is
  audited and broadcast so the transcript shows what happened.
* ``GUARDIAN`` — an LLM reviewer (:mod:`omniagents.core.agents.guardian`)
  assesses the exact planned action and approves or denies it in the
  user's stead ("Approve for me").
* ``HUMAN`` — today's behavior: broadcast an approval prompt and await the
  client's response.

The resolver reads its policy from the layered configuration on every call
(``approvals.*`` fields are ``reload=hot``) and the session's per-conversation
reviewer override (``session.approvals_reviewer``). Environment identity comes
from the session's thread binding; a session with no binding, an unresolved
automatic selection, or any lookup failure resolves as HOST — the
conservative tier that never auto-approves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

REVIEWER_USER = "user"
REVIEWER_AUTO = "auto"
REVIEWERS = (REVIEWER_USER, REVIEWER_AUTO)

SANDBOX_AUTO_NEVER = "never"
SANDBOX_AUTO_NO_NETWORK = "no_network"
SANDBOX_AUTO_ALWAYS = "always"


class ApprovalRoute(Enum):
    AUTO_APPROVE = "auto_approve"
    GUARDIAN = "guardian"
    HUMAN = "human"


@dataclass(frozen=True)
class EnvironmentSummary:
    """What the approval layer knows about where the tool would run.

    ``kind`` is the :class:`~omniagents.execution.EnvironmentKind` value
    (``"host"``, ``"devbox"``, …) or ``None`` when the session has no
    resolved environment — treated exactly like HOST everywhere.
    """

    kind: str | None
    capabilities: frozenset[str]
    environment_id: str | None = None
    generation: int | None = None

    @property
    def is_sandboxed(self) -> bool:
        return self.kind is not None and self.kind != "host"

    @property
    def has_network(self) -> bool:
        return "network" in self.capabilities


HOST_ENVIRONMENT = EnvironmentSummary(kind=None, capabilities=frozenset())


async def resolve_environment_summary(service: Any, session: Any) -> EnvironmentSummary:
    """The session's live environment identity, failing closed to HOST.

    Both lookups are in-process registry reads, so this is cheap enough to
    run on every approval — no cache to invalidate when the environment is
    rebuilt under a new generation.
    """
    try:
        binding = await service.thread_bindings.get(session.id)
    except Exception:
        return HOST_ENVIRONMENT
    environment_id = getattr(binding, "environment_id", None)
    if not environment_id:
        return HOST_ENVIRONMENT
    try:
        descriptor = await service.environment_manager.describe(environment_id)
    except Exception:
        return HOST_ENVIRONMENT
    try:
        return EnvironmentSummary(
            kind=descriptor.kind.value,
            capabilities=frozenset(
                capability.value for capability in descriptor.capabilities
            ),
            environment_id=descriptor.environment_id,
            generation=descriptor.generation,
        )
    except Exception:
        return HOST_ENVIRONMENT


def effective_reviewer(service: Any, session: Any) -> str:
    """Session override first, then the layered-config process default."""
    session_reviewer = getattr(session, "approvals_reviewer", None)
    if session_reviewer in REVIEWERS:
        return session_reviewer
    return _config_value(service, "approvals.reviewer", REVIEWER_USER, REVIEWERS)


def sandbox_auto_approve_tier(service: Any) -> str:
    return _config_value(
        service,
        "approvals.sandbox_auto_approve",
        SANDBOX_AUTO_NO_NETWORK,
        (SANDBOX_AUTO_NEVER, SANDBOX_AUTO_NO_NETWORK, SANDBOX_AUTO_ALWAYS),
    )


def guardian_model_ref(service: Any) -> str | None:
    value = _config_value(service, "approvals.guardian_model", "", None)
    value = value.strip() if isinstance(value, str) else ""
    return value or None


def resolve_route(
    *, reviewer: str, sandbox_tier: str, environment: EnvironmentSummary
) -> ApprovalRoute:
    """Pure routing decision — see the module docstring for the tiers."""
    if environment.is_sandboxed:
        if sandbox_tier == SANDBOX_AUTO_ALWAYS:
            return ApprovalRoute.AUTO_APPROVE
        if sandbox_tier == SANDBOX_AUTO_NO_NETWORK and not environment.has_network:
            return ApprovalRoute.AUTO_APPROVE
    if reviewer == REVIEWER_AUTO:
        return ApprovalRoute.GUARDIAN
    return ApprovalRoute.HUMAN


async def resolve_approval_route(
    service: Any, session: Any
) -> tuple[ApprovalRoute, EnvironmentSummary]:
    environment = await resolve_environment_summary(service, session)
    route = resolve_route(
        reviewer=effective_reviewer(service, session),
        sandbox_tier=sandbox_auto_approve_tier(service),
        environment=environment,
    )
    return route, environment


def _config_value(
    service: Any, key: str, default: Any, allowed: tuple[str, ...] | None
) -> Any:
    try:
        is_set, value = service._layered_config_store().effective_value(key)
    except Exception:
        return default
    if not is_set:
        return default
    if allowed is not None and value not in allowed:
        return default
    return value
