"""Standard connection-lifecycle policy shared by all maintained GUI clients.

This module is the single source of truth for client lifecycle behavior:
connect timeouts, RPC call timeouts, WebSocket ping/pong, the reconnect
backoff schedule, and the classification of failures into *retryable*
versus *permanent*.

The TypeScript twin of this module lives at
``omniagents/backends/web/ui/src/rpc/lifecycle.ts`` and
``omniagents/backends/ink/tui/src/rpc/lifecycle.ts`` (byte-identical
copies, enforced by ``tests/unit/rpc/test_lifecycle.py``). If you change a
default here, change it there too — the cross-language consistency test
will fail otherwise.

See ``omniagents/rpc/protocol.md`` §"Connection Lifecycle" for the
normative description of the policy, including idempotency guidance for
retries.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Literal, Optional

FailureClass = Literal["retryable", "permanent", "indeterminate"]


@dataclass(frozen=True)
class LifecyclePolicy:
    """Client lifecycle policy expressed as data.

    All maintained clients derive their behavior from these values so the
    fleet stays consistent by construction. Durations are seconds.
    """

    #: Maximum time to establish the transport *and* finish the
    #: ``initialize`` handshake.
    connect_timeout: float = 10.0
    #: Default deadline for a single JSON-RPC request/response round trip.
    rpc_timeout: float = 60.0
    #: WebSocket ping cadence (liveness probe).
    ping_interval: float = 20.0
    #: How long to wait for the pong before declaring the link dead.
    ping_timeout: float = 10.0
    #: First reconnect delay.
    reconnect_initial_delay: float = 0.5
    #: Exponential multiplier applied per attempt.
    reconnect_multiplier: float = 2.0
    #: Ceiling on the per-attempt delay.
    reconnect_max_delay: float = 30.0
    #: Full-jitter fraction applied to each delay (0.1 = ±10%).
    reconnect_jitter: float = 0.1
    #: Give up (permanent failure) after this many consecutive failures.
    reconnect_max_attempts: int = 10


DEFAULT_LIFECYCLE_POLICY = LifecyclePolicy()


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

#: JSON-RPC error codes that a client must NEVER retry: resending the same
#: request will fail the same way. Covers malformed/invalid requests and the
#: typed handshake errors introduced by the GUI protocol initialize flow.
PERMANENT_RPC_ERROR_CODES = frozenset(
    {
        -32700,  # ParseError
        -32600,  # InvalidRequest
        -32601,  # MethodNotFound
        -32602,  # InvalidParams
        -32010,  # InitializationRequired
        -32011,  # AlreadyInitialized
        -32012,  # ProtocolVersionMismatch
        -32013,  # CapabilityMismatch
    }
)

#: JSON-RPC error codes that signal a transient condition. Retry is safe for
#: idempotent methods (honor ``data.retry_after_ms`` when present).
RETRYABLE_RPC_ERROR_CODES = frozenset(
    {
        -32020,  # TransportOverloaded
    }
)

#: WebSocket close codes that must terminate the connection permanently
#: (no reconnect). 4403 is the server's authentication/authorization
#: rejection and 4404 its feature-not-available rejection — both are
#: deterministic, so retrying loops forever without ever succeeding.
PERMANENT_CLOSE_CODES = frozenset(
    {
        1002,  # protocol error
        1003,  # unsupported data
        1008,  # policy violation
        4400,  # bad request (reserved)
        4403,  # authentication / authorization failed
        4404,  # feature not available on this server
    }
)


def classify_rpc_error_code(code: int) -> FailureClass:
    """Classify a JSON-RPC error code for retry purposes.

    ``indeterminate`` codes (e.g. -32603 InternalError, application codes)
    must be surfaced to the caller rather than auto-retried.
    """
    if code in PERMANENT_RPC_ERROR_CODES:
        return "permanent"
    if code in RETRYABLE_RPC_ERROR_CODES:
        return "retryable"
    return "indeterminate"


def classify_close_code(code: Optional[int]) -> FailureClass:
    """Classify a WebSocket close code.

    Any 44xx application close code is treated as permanent — that range is
    reserved for deterministic server rejections (auth, missing feature).
    Everything else (1000/1001/1006/1011/1012/1013, or no code at all) is
    retryable: the next attempt may land on a healthy server.
    """
    if code is None:
        return "retryable"
    if code in PERMANENT_CLOSE_CODES or 4400 <= code <= 4499:
        return "permanent"
    return "retryable"


# ---------------------------------------------------------------------------
# Reconnect backoff
# ---------------------------------------------------------------------------


def reconnect_delay(
    policy: LifecyclePolicy,
    attempt: int,
    rng: Callable[[], float] = random.random,
) -> float:
    """Delay (seconds) before reconnect ``attempt`` (1-based).

    Exponential backoff with a ceiling and symmetric jitter:
    ``min(initial * multiplier**(attempt-1), max) * (1 ± jitter)``.
    """
    if attempt < 1:
        attempt = 1
    base = policy.reconnect_initial_delay * (
        policy.reconnect_multiplier ** (attempt - 1)
    )
    base = min(base, policy.reconnect_max_delay)
    if policy.reconnect_jitter <= 0:
        return base
    spread = policy.reconnect_jitter * base
    return base - spread + rng() * 2 * spread


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RpcConnectionClosedError(ConnectionError):
    """The connection closed while (or before) a call was outstanding.

    Every pending call is rejected with this error when the connection is
    lost — no promise/future may remain unresolved after a disconnect.

    Attributes:
        permanent: True when the close is terminal (auth rejection,
            protocol violation, retry budget exhausted) and the client will
            not reconnect.
        close_code: WebSocket close code when known.
        reason: Human-readable close reason when known.
    """

    def __init__(
        self,
        message: str = "Connection closed",
        *,
        permanent: bool = False,
        close_code: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.close_code = close_code
        self.reason = reason


class RpcTimeoutError(TimeoutError):
    """A JSON-RPC call did not complete within its deadline."""

    def __init__(self, method: str, timeout: float) -> None:
        super().__init__(f"RPC call {method!r} timed out after {timeout:g}s")
        self.method = method
        self.timeout = timeout


# ---------------------------------------------------------------------------
# Reconnect controller
# ---------------------------------------------------------------------------


class ReconnectController:
    """Pure state machine driving policy-compliant reconnect loops.

    Owns nothing but counters — callers perform the actual connect and
    sleep. Typical loop::

        ctrl = ReconnectController(policy)
        while True:
            try:
                await connect()
                ctrl.record_success()
                break
            except SomeError as exc:
                decision = ctrl.record_failure(permanent=is_permanent(exc))
                if not decision.retry:
                    raise
                await asyncio.sleep(decision.delay)

    Resume-from-cursor semantics after a successful reconnect are
    intentionally out of scope: callers plug replay behavior in via their
    own post-reconnect hook (see ``JsonRpcClient.on_disconnect``).
    """

    @dataclass(frozen=True)
    class Decision:
        retry: bool
        delay: float
        attempt: int
        permanent: bool

    def __init__(
        self,
        policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._policy = policy
        self._rng = rng
        self._attempts = 0

    @property
    def attempts(self) -> int:
        return self._attempts

    def record_success(self) -> None:
        """Reset the failure counter after a healthy connection."""
        self._attempts = 0

    def record_failure(
        self, *, permanent: bool = False
    ) -> "ReconnectController.Decision":
        """Register a failed attempt and decide whether to retry."""
        self._attempts += 1
        exhausted = self._attempts >= self._policy.reconnect_max_attempts
        if permanent or exhausted:
            return self.Decision(
                retry=False, delay=0.0, attempt=self._attempts, permanent=True
            )
        return self.Decision(
            retry=True,
            delay=reconnect_delay(self._policy, self._attempts, self._rng),
            attempt=self._attempts,
            permanent=False,
        )
