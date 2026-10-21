"""Process-isolation sandbox backend backed by the ``omni-sandbox`` CLI.

Behaves like the extended ``unix_local`` backend (same workspace
materialization, PTY streaming, lifecycle wrapper) except every exec —
batched ``exec``, PTY shells, and the exec-based file ops — is wrapped in
the standalone `omni-sandbox <https://github.com/>`_ binary::

    omni-sandbox exec --workspace <manifest root>
        [--net]                       # only when network: unrestricted
        [--protect SUBPATH]...        # per profile protected subpath
        [--ro-bind DIR]... [--rw-bind DIR]...
        -- <command...>

which gives platform-native process isolation (Linux: bubblewrap + seccomp;
macOS: seatbelt; Windows: AppContainer) with fail-closed network semantics.

YAML profile schema (``sandbox.client``)::

    client:
      type: process
      binary: /path/to/omni-sandbox   # optional explicit path
      ro_binds: [/opt/tools]          # extra read-only mounts
      rw_binds: [~/.cache/thing]      # extra read-write mounts
      network: none | unrestricted    # default: none (blocked)
      network_allow: [host, ...]      # HARD ERROR — see below
      protected_subpaths: [.git, .env]  # omit → binary defaults apply

Binary discovery (construction time, fail closed): the explicit
``binary`` profile key → the ``OMNI_SANDBOX_BIN`` env var → ``PATH``.
A missing binary raises at construction with the install options named —
never at first exec, deep inside an agent run.

Network is all-or-nothing by design: the process sandbox cannot enforce a
host-filtered egress allowlist (bwrap/sandbox-exec/AppContainer have no
reliable per-host filtering), and the omni-sandbox CLI hard-fails on
``--net-allow``. A profile that demands ``network_allow`` therefore fails
closed at construction with a clear error (mirroring omni-code's
governance message) instead of silently granting full network access.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.sandbox.sandboxes.unix_local import (
    UnixLocalSandboxSession,
    UnixLocalSandboxSessionState,
)

from omniagents.core.sandbox.process_relaunch import (
    SandboxBind,
    omni_sandbox_exec_argv,
)
from omniagents.core.sandbox.unix_local_extended import (
    ExtendedUnixLocalSandboxClient,
    ExtendedUnixLocalSandboxSession,
)

__all__ = [
    "ProcessSandboxClient",
    "ProcessSandboxSession",
    "resolve_omni_sandbox_binary",
]


def resolve_omni_sandbox_binary(explicit: str | None = None) -> str:
    """Resolve the omni-sandbox binary path, failing closed when absent.

    Order: *explicit* (the profile's ``client.binary`` key) →
    ``OMNI_SANDBOX_BIN`` env var → ``PATH``. Raises
    :class:`FileNotFoundError` with the install options named when no
    candidate resolves to an executable file.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)
        raise FileNotFoundError(
            f"sandbox.client.binary points to {explicit!r}, which is not an "
            "executable file. Build the binary in the omni-sandbox repo "
            "(cargo build --release) or fix the path."
        )

    env_bin = os.getenv("OMNI_SANDBOX_BIN")
    if env_bin:
        path = Path(env_bin).expanduser()
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)
        raise FileNotFoundError(
            f"OMNI_SANDBOX_BIN points to {env_bin!r}, which is not an "
            "executable file. Build the binary in the omni-sandbox repo "
            "(cargo build --release) or fix the variable."
        )

    on_path = shutil.which("omni-sandbox")
    if on_path:
        return on_path

    raise FileNotFoundError(
        "Sandbox client 'process' requires the omni-sandbox binary, but none "
        "was found. Install options: set sandbox.client.binary to an explicit "
        "path, set the OMNI_SANDBOX_BIN env var, or put omni-sandbox on PATH "
        "(build it from the standalone omni-sandbox repo with: "
        "cargo build --release)."
    )


class ProcessSandboxSession(ExtendedUnixLocalSandboxSession):
    """Extended unix_local session whose every exec runs under omni-sandbox.

    The SDK funnels batched execs (``_exec_internal``), PTY starts
    (``pty_exec_start``), and exec-based file ops through
    ``_confined_exec_command`` — the same seam the SDK uses for its
    darwin seatbelt wrapping. Overriding it here wraps every process in
    ``omni-sandbox exec`` on all platforms (omni-sandbox brings its own
    seatbelt profile on darwin, so the SDK's sandbox-exec wrapping is
    replaced, not stacked).

    The invocation config is stamped by :class:`ProcessSandboxClient`
    right after construction (state round-trips through the SDK's plain
    ``UnixLocalSandboxSessionState``, which has no slot for it).
    """

    # Stamped by ProcessSandboxClient._make_inner_session.
    _omni_sandbox_binary: str = "omni-sandbox"
    _omni_sandbox_ro_binds: tuple[str, ...] = ()
    _omni_sandbox_rw_binds: tuple[str, ...] = ()
    _omni_sandbox_allow_net: bool = False
    _omni_sandbox_protected_subpaths: tuple[str, ...] | None = None

    def _confined_exec_command(
        self,
        *,
        command_parts: list[str],
        workspace_root: Path,
        env: Mapping[str, str],
    ) -> list[str]:
        _ = env  # host env is inherited through the wrapper process
        # Canonical argv construction is shared with the governance relaunch
        # path (process_relaunch) so the wire format cannot drift. The
        # builder emits one --protect per subpath (comma-joined would be
        # read as a single, wrong subpath name); None → no flag, so the
        # binary's own defaults (.git/.env/.codex) apply.
        return omni_sandbox_exec_argv(
            self._omni_sandbox_binary,
            str(workspace_root),
            allow_net=self._omni_sandbox_allow_net,
            protected_subpaths=self._omni_sandbox_protected_subpaths or (),
            binds=[
                *(SandboxBind(m) for m in self._omni_sandbox_ro_binds),
                *(SandboxBind(m, writable=True) for m in self._omni_sandbox_rw_binds),
            ],
            command=command_parts,
        )


class ProcessSandboxClient(ExtendedUnixLocalSandboxClient):
    """``unix_local``-shaped sandbox client with omni-sandbox process isolation.

    Construction fails closed: a missing binary or a ``network_allow``
    host allowlist raises immediately (see module docstring). Pause /
    unpause are not implemented — the lifecycle wrapper's probing proxies
    report them unsupported, exactly like plain ``unix_local``.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        ro_binds: Sequence[str] = (),
        rw_binds: Sequence[str] = (),
        network: str = "none",
        network_allow: Sequence[str] = (),
        protected_subpaths: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if network_allow:
            hosts = ", ".join(str(h) for h in network_allow)
            raise ValueError(
                "Sandbox profile requires a host-filtered network allowlist "
                f"({hosts}), but the process sandbox backend cannot enforce "
                "host-level network filtering (and omni-sandbox hard-fails "
                "on --net-allow by design). Use a container backend that "
                "supports network allowlisting (client.type: docker), set "
                "network: unrestricted, or remove network_allow from the "
                "profile."
            )
        if network not in ("none", "unrestricted"):
            raise ValueError(
                f"sandbox.client.network must be 'none' or 'unrestricted', "
                f"got {network!r}"
            )

        self._binary = resolve_omni_sandbox_binary(binary)
        self._ro_binds = tuple(str(p) for p in ro_binds)
        self._rw_binds = tuple(str(p) for p in rw_binds)
        self._allow_net = network == "unrestricted"
        self._protected_subpaths = (
            None
            if protected_subpaths is None
            else tuple(str(s) for s in protected_subpaths)
        )
        super().__init__(**kwargs)

    def _make_inner_session(
        self, state: UnixLocalSandboxSessionState
    ) -> UnixLocalSandboxSession:
        session = ProcessSandboxSession.from_state(state)
        # Dynamic attributes consumed by the omni-sandbox exec wrapper.
        session._omni_sandbox_binary = self._binary  # type: ignore[attr-defined]
        session._omni_sandbox_ro_binds = self._ro_binds  # type: ignore[attr-defined]
        session._omni_sandbox_rw_binds = self._rw_binds  # type: ignore[attr-defined]
        session._omni_sandbox_allow_net = self._allow_net  # type: ignore[attr-defined]
        session._omni_sandbox_protected_subpaths = (  # type: ignore[attr-defined]
            self._protected_subpaths
        )
        return session
