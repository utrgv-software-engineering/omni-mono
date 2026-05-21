"""Protocol for sandbox sessions that support PTY streaming + resize.

The SDK's ``SandboxSession`` exposes ``pty_exec_start`` / ``pty_write_stdin``
/ ``pty_terminate_all`` — enough for the agent's bash tool, which uses
batched poll-then-collect semantics. For interactive terminals (a human
holding a shell open over a WebSocket) we need two more capabilities:

* **Streaming output** — bytes-as-they-arrive instead of yield-time poll.
* **Resize** — propagate xterm winsize changes into the PTY.

These primitives live on omniagents subclasses of the SDK sessions
(:class:`ExtendedUnixLocalSandboxSession`,
:class:`ExtendedDockerSandboxSession`). Backends without an implementation
raise :class:`PtyStreamingUnavailable` from
:func:`coerce_pty_streaming`, which the terminal manager surfaces as a
clear "this sandbox backend doesn't support terminals yet" error.

We keep this contract on a Protocol rather than a base class so the SDK
sessions can be subclassed independently per backend; runtime checks use
``isinstance`` against ``runtime_checkable`` Protocols.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol, runtime_checkable


class PtyStreamingUnavailable(RuntimeError):
    """Raised when a session backend doesn't support streaming PTYs."""


@runtime_checkable
class PtyStreamingSession(Protocol):
    """Capability subset of a sandbox session that supports streaming PTYs.

    Implementations live on the omniagents extended session classes and
    extend the SDK's PTY surface with two new methods. ``process_id`` is
    the same integer the SDK's ``pty_exec_start`` returns and that
    ``pty_write_stdin`` / ``pty_terminate_all`` already accept.
    """

    def pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        """Stream raw bytes from the PTY as they arrive.

        The iterator yields bytes chunks (whatever the underlying read
        produced; chunks are not normalized). Chunks include both stdout
        and stderr (they share the PTY). The iterator completes when the
        process exits or the PTY is closed; cancelling the iterator
        unregisters the consumer without affecting other consumers.

        Pre-buffered chunks captured before the first consumer attached
        are delivered to that consumer (the implementation drains the
        SDK's ``output_chunks`` deque at attach time so a shell's initial
        prompt isn't lost between ``pty_exec_start`` returning and the
        first ``pty_stream_output`` call).
        """
        ...

    async def pty_resize(self, process_id: int, *, cols: int, rows: int) -> None:
        """Resize the PTY's winsize. No-op when the process has exited."""
        ...


def coerce_pty_streaming(session: object) -> Optional[PtyStreamingSession]:
    """Return *session* (unwrapping the SDK wrapper) iff it supports streaming.

    The SDK's :class:`SandboxSession` is a thin proxy around an inner
    backend-specific session held on ``_inner``. The streaming primitives
    live on the inner session, so we unwrap before the isinstance check.
    Returns ``None`` when the session doesn't satisfy the protocol — the
    caller decides whether to fall back or raise.
    """
    inner = getattr(session, "_inner", session)
    if isinstance(inner, PtyStreamingSession):
        return inner
    return None
