"""The wasm sandbox: bashkit shell + pyodide Python as one sandbox.

Server-side composition of the two wasm runtimes this package already
ships, sharing one workspace:

* **Shell + files** — :class:`~omniagents.core.sandbox.bashkit_engine.
  BashkitSandboxSession`: bash compiled to wasm, run in-process, with the
  workspace mounted from a real host directory (``FileSystem.real``).
* **Python** — the pyodide Node worker from
  :mod:`omniagents.workspace.pyodide`: CPython compiled to wasm in a
  subprocess, with the *same* host directory NODEFS-mounted at the same
  VFS path. Shell and Python agree on one uniform ``/workspace``
  namespace, and the host path never appears inside the sandbox — the
  docker container's property, without a container. (The engine's
  ``host_dir`` split translates paths for the host-side git/curl
  builtins.) What the shell writes, Python reads, and vice versa — one
  filesystem, no copying.

The ``python``/``python3`` command inside the shell dispatches to the
pyodide worker. This is the same composition the book platform proves out
in the browser, with one crucial difference: there,
``make_python_builtin`` runs scripts in the *surrounding* interpreter,
which is safe only because that interpreter is itself wasm. On a server
the surrounding interpreter is the serve process — running agent-authored
code there would puncture the containment this backend exists to provide.
So ``real_python=False`` always, and Python goes to the worker.

Containment story (why this backend registers the ``contained``
capability): bashkit has no syscalls into the host — its only reach is
the mounted workspace subtree and its HTTP builtins. Pyodide's Emscripten
FS contains only the NODEFS mount of that same subtree, and its only
egress is the JS ``fetch`` the worker exposes. Neither runtime can exec
host processes. Network is OFF by default (unlike the docker devbox) and
governed by one policy across both runtimes: an unconfigured sandbox
boots contained-and-offline — no NETWORK capability, which lets
"Approve for me" sessions run promptless (the reviewer choice itself is
never overridden) — while staying toggleable live via
``sandbox.set_network``. Explicit grants (``allow_all`` or a
host-filtering allowlist) boot with egress on; ``network: none`` is the
hard opt-out that deletes the egress surfaces and has no toggle.

Known limits, stated plainly: no real binaries (only bashkit's utilities
plus git/curl/python builtins), no processes or services, no PTY (the
terminal app reports unsupported), and ``python`` ignores shell stdin —
``echo hi | python -c ...`` sees no input. Each ``python`` invocation
executes in a fresh module namespace (real interpreter semantics), while
the underlying pyodide interpreter — and any packages it has loaded —
stays warm across invocations.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import PurePosixPath
from typing import Any, List, Optional, Sequence

from agents.sandbox.manifest import Manifest
from agents.sandbox.session.sandbox_client import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
)
from agents.sandbox.session.sandbox_session import SandboxSession
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.snapshot import NoopSnapshot

from omniagents.core.sandbox.bashkit_engine import BashkitSandboxSession
from omniagents.core.sandbox.lifecycle_session import LifecycleSessionClientMixin

#: Wall-clock budget for one ``python`` invocation from the shell.
PYTHON_EXEC_TIMEOUT_SECONDS = 120.0


def _pyodide_network_policy(network: Any) -> tuple[str, tuple[str, ...]]:
    """Map the profile's ``network`` block to the *configured* policy.

    This is the policy egress follows while the toggle is ON — whether the
    session *boots* with egress is :func:`_wasm_network_boots_blocked`'s
    question (absent boots offline; the toggle can grant later). The same
    resolution feeds :class:`BashkitSandboxSession`'s curl/git builtins, so
    both runtimes obey one policy:

    * absent (``None``) → ``("open", ())`` — what the toggle restores when
      the user enables network on a default (off-by-default) sandbox.
    * ``False`` / ``"none"`` → ``("none", ())`` — the HARD opt-out: no
      egress machinery exists at all and the toggle reads unsupported.
    * ``True`` / ``{"allow_all": true}`` → ``("open", ())``.
    * ``{"allow": [hosts]}`` → ``("allow", hosts)`` — host-filtered.
    """
    if network is None:
        return "open", ()
    if network is False or network == "none":
        return "none", ()
    if network is True:
        return "open", ()
    spec = dict(network) if isinstance(network, dict) else {}
    if spec.get("allow_all"):
        return "open", ()
    allow = spec.get("allow")
    if isinstance(allow, (list, tuple)) and allow:
        return "allow", tuple(str(h).strip() for h in allow if str(h).strip())
    # A restriction was written but names nothing concrete (``{}``,
    # ``{"allow": []}``): read it as the explicit opt-out, not silent open.
    return "none", ()


def _wasm_network_boots_blocked(network: Any) -> bool:
    """True when the session starts with egress gated.

    Network is OFF by default — an unconfigured wasm sandbox registers as
    contained-and-offline (promptless in "Approve for me" sessions) and
    the live toggle (``sandbox.set_network``) can grant its configured
    policy later. Explicit grants (``true``, ``allow_all``, an ``allow``
    list — including the launcher's Settings allowlist injection) boot
    with egress on; the explicit opt-outs boot hard-off with no toggle.
    """
    if network is None:
        return True
    mode, _ = _pyodide_network_policy(network)
    return mode == "none"


def _bashkit_network(mode: str, allow: tuple[str, ...]) -> Any:
    """Translate the resolved policy back into a bashkit ``network=`` arg."""
    if mode == "open":
        return True
    if mode == "allow":
        return {"allow": list(allow)}
    return None  # "none" → no http builtins


class WasmSandboxClientOptions(BaseSandboxClientOptions):
    """No wasm-specific options today; exists for factory symmetry."""

    type: str = "wasm"


class WasmSandboxSession(BashkitSandboxSession):
    """Bashkit session whose ``python`` command runs in a pyodide worker."""

    def __init__(
        self,
        *,
        root: str,
        host_dir: str | None = None,
        manifest: Manifest,
        session_id: str = "wasm",
        git: Any = False,
        network: Any = None,
        node_path: str = "node",
        packages: Sequence[str] = (),
        **bash_kwargs: Any,
    ) -> None:
        builtins = dict(bash_kwargs.pop("custom_builtins", None) or {})
        runner = self._make_python_dispatch()
        builtins["python"] = runner
        builtins["python3"] = runner
        # One resolved policy for both runtimes so the shell's curl and
        # Python's egress can never disagree. Absent boots OFFLINE (off by
        # default, unlike the docker devbox) but stays toggleable to the
        # configured policy; explicit `none` is hard-off with no toggle.
        py_network, py_allow = _pyodide_network_policy(network)
        bash_network = _bashkit_network(py_network, py_allow)
        boots_blocked = _wasm_network_boots_blocked(network) and py_network != "none"
        # Remembered for the live toggle: re-enabling restores the profile's
        # configured policy (open or allowlist), never more.
        self._configured_network: tuple[str, tuple[str, ...]] = (py_network, py_allow)
        super().__init__(
            root=root,
            host_dir=host_dir,
            session_id=session_id,
            git=git,
            network=bash_network,
            # NEVER the host interpreter — see the module docstring.
            real_python=False,
            custom_builtins=builtins,
            **bash_kwargs,
        )
        # The bashkit base built a bare manifest; carry the real one so the
        # SDK's start() materializes its entries through our file ops.
        self.state = SandboxSessionState(
            type="wasm",
            snapshot=NoopSnapshot(id=session_id),
            manifest=manifest,
        )
        from omniagents.workspace.pyodide import _PyodideWorker

        # Pyodide NODEFS-mounts the host backing dir at the SAME VFS path
        # bashkit presents (``root``, normally /workspace). Shell and python
        # therefore share one uniform namespace and the host path never
        # appears inside the sandbox — the docker container's property,
        # without a container.
        #
        # Network is the SAME policy that gates the bashkit curl/git
        # builtins, applied to pyodide's js.fetch egress — otherwise an
        # explicitly offline sandbox (which auto-approves on that basis)
        # would still leak through Python.
        self._worker = _PyodideWorker(
            host_workspace_dir=self._host_dir or root,
            node_path=node_path,
            packages=tuple(packages),
            mount_point=root,
            network=py_network,
            network_allow=py_allow,
            network_blocked=boots_blocked,
        )
        self._worker_started = False
        # Off-by-default: gate the bashkit builtins from the first exec; the
        # worker booted with its gate blocked via the flag above.
        self._network_blocked = boots_blocked
        # Captured at backend start; the python builtin runs on a worker
        # thread (bashkit execute_sync is pushed off the loop) and calls
        # back into the loop with run_coroutine_threadsafe.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------ lifecycle ------------------------------ #

    def describe(self) -> str:
        return f"wasm sandbox (bashkit + pyodide) at {self.state.manifest.root}"

    # --------------------------- network toggle ---------------------------- #

    @property
    def network_toggleable(self) -> bool:
        """False for explicitly-offline profiles (``network: none``).

        Those sandboxes registered as contained-and-offline — the state
        "Approve for me" sessions run promptless on — so there is
        deliberately no way to grant egress mid-session. Mirrors docker's
        ``network_mode: none`` reading as toggle-unsupported.
        """
        return self._configured_network[0] != "none"

    @property
    def network_enabled(self) -> bool:
        """Current toggle state (True until ``set_network_enabled(False)``)."""
        return not self._network_blocked

    async def set_network_enabled(self, enabled: bool) -> None:
        """Live offline toggle (``sandbox.set_network``) for both runtimes.

        One flip gates the shell's curl/wget and git remote subcommands
        (the bashkit flag) and Python's egress (the pyodide worker's
        gated globals). Enabling restores the profile's configured policy
        — open or allowlist — never more. State lives with the session,
        which for a wasm sandbox IS the sandbox lifetime (no durable
        runtime to persist a marker into, unlike docker).
        """
        if not self.network_toggleable:
            raise RuntimeError(
                "network toggle unsupported: profile is explicitly offline"
            )
        self._network_blocked = not enabled
        mode, allow = self._configured_network if enabled else ("none", ())
        await self._worker.set_network(mode, allow)

    async def _ensure_backend_started(self) -> None:
        await super()._ensure_backend_started()
        self._loop = asyncio.get_running_loop()
        if not self._worker_started:
            await self._worker.start()
            root = str(self.state.manifest.root)
            await self._worker.call(
                "exec_python",
                {
                    "code": f"import os; os.chdir({root!r})",
                    "capture_stdout": False,
                },
            )
            self._worker_started = True

    async def _shutdown_backend(self) -> None:
        try:
            await self._worker.close()
        finally:
            self._worker_started = False
            await super()._shutdown_backend()

    # -------------------------------- exec --------------------------------- #

    async def _exec_internal(self, *command, timeout=None):
        # Workspace seeding (PermissiveLocalDir / LocalGitArchive) ships one
        # tar via ``session.write`` and extracts it with an argv exec —
        # exactly ``tar xf <staging> -C <dest>``. bashkit has no real tar
        # (and its utilities don't cross the FileSystem.real mount), but the
        # workspace IS a host directory, so extract host-side — the same
        # trick the bashkit engine already plays for find/grep.
        parts = [str(part) for part in command]
        if len(parts) == 5 and parts[:2] == ["tar", "xf"] and parts[3] == "-C":
            return await asyncio.to_thread(self._extract_tar, parts[2], parts[4])
        return await super()._exec_internal(*command, timeout=timeout)

    def _extract_tar(self, archive_path: str, dest_path: str):
        from agents.sandbox.types import ExecResult as _SdkExecResult

        archive = self._confined_mounted_root(archive_path)
        dest = self._confined_mounted_root(dest_path)
        if archive is None or dest is None:
            return _SdkExecResult(
                stdout=b"", stderr=b"tar: path outside workspace\n", exit_code=1
            )
        import tarfile

        try:
            os.makedirs(dest, exist_ok=True)
            with tarfile.open(archive) as handle:
                # "data" filter: reject members escaping via ../ or links,
                # strip setuid bits — the archive was built by our own entry
                # classes, but stay safe against a crafted workspace tar.
                handle.extractall(dest, filter="data")
        except Exception as exc:  # noqa: BLE001 — surface as command failure
            return _SdkExecResult(
                stdout=b"", stderr=f"tar: {exc}\n".encode(), exit_code=1
            )
        return _SdkExecResult(stdout=b"", stderr=b"", exit_code=0)

    # ------------------------------- python -------------------------------- #

    async def _run(self, script: str):
        # Off the event loop, unlike the bashkit base: the python builtin
        # blocks its thread waiting on the pyodide worker, and the worker's
        # stdio reader lives on the loop — running execute_sync on the loop
        # would deadlock the two.
        return await asyncio.to_thread(self.bash.execute_sync, script)

    def _make_python_dispatch(self):
        """The shell's ``python``/``python3``: parse argv, run in the worker.

        Mirrors ``make_python_builtin``'s argv contract (``-c code``,
        script path, ``--version``) but ships the program to the pyodide
        subprocess instead of the host interpreter.
        """

        def python(ctx: Any):
            from bashkit import BuiltinResult

            argv = [str(a) for a in ctx.argv]
            cwd = str(getattr(ctx, "cwd", "") or self.state.manifest.root)

            code: Optional[str] = None
            script: Optional[str] = None
            args: List[str] = []
            index = 0
            while index < len(argv):
                token = argv[index]
                if token == "-c" and index + 1 < len(argv):
                    code = argv[index + 1]
                    args = argv[index + 2 :]
                    break
                if token in ("--version", "-V"):
                    return BuiltinResult(stdout="Python (pyodide)\n")
                if token.startswith("-"):
                    index += 1
                    continue
                script = token
                args = argv[index + 1 :]
                break

            if code is None and script is None:
                return BuiltinResult(
                    stderr="python3: interactive mode is unavailable here; "
                    "pass a script or -c\n",
                    exit_code=2,
                )

            # Shell and worker share one VFS namespace (pyodide mounts the
            # backing dir at the same root bashkit presents), so the worker
            # receives VFS paths verbatim. Existence and confinement are
            # checked host-side: paths outside the workspace are invisible
            # to the worker and report not-found instead of reading the
            # wrong file.
            root = str(self.state.manifest.root)
            host_cwd = self._confined_mounted_root(cwd)
            mapped_cwd = self._virtual_path(host_cwd) if host_cwd else root
            mapped_script: Optional[str] = None
            if script is not None:
                vfs_target = (
                    script
                    if script.startswith("/")
                    else str(PurePosixPath(cwd, script))
                )
                host_target = self._confined_mounted_root(vfs_target)
                if host_target is None or not os.path.isfile(host_target):
                    return BuiltinResult(
                        stderr=f"python3: can't open file '{script}': "
                        "[Errno 2] No such file or directory\n",
                        exit_code=2,
                    )
                mapped_script = self._virtual_path(host_target)

            program = _build_worker_program(
                cwd=mapped_cwd,
                script=mapped_script,
                code=code,
                args=args,
            )
            if self._loop is None or not self._worker_started:
                return BuiltinResult(
                    stderr="python3: the python runtime is not running\n",
                    exit_code=1,
                )
            future = asyncio.run_coroutine_threadsafe(
                self._worker.call(
                    "exec_python",
                    {
                        "code": program,
                        "capture_stdout": True,
                        "load_imports": True,
                        "timeout_s": PYTHON_EXEC_TIMEOUT_SECONDS,
                    },
                    timeout=PYTHON_EXEC_TIMEOUT_SECONDS + 5.0,
                ),
                self._loop,
            )
            try:
                result = future.result(timeout=PYTHON_EXEC_TIMEOUT_SECONDS + 10.0)
            except Exception as exc:  # noqa: BLE001 — surface as command failure
                return BuiltinResult(
                    stderr=f"python3: {exc}\n",
                    exit_code=1,
                )
            return BuiltinResult(
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                exit_code=int(result.get("exit_code", 0)),
            )

        return python


def _build_worker_program(
    *,
    cwd: str,
    script: Optional[str],
    code: Optional[str],
    args: List[str],
) -> str:
    """Wrap one shell ``python`` invocation for ``exec_python``.

    Sets argv/cwd like a real interpreter launch and restores them after.
    The worker's exit-code channel is binary (0 on success, 1 on any
    uncaught exception), so ``sys.exit(0)`` is swallowed and a nonzero
    exit surfaces as a plain ``RuntimeError`` — deliberately NOT a
    re-raised ``SystemExit``, which poisons pyodide's interpreter state
    and makes every later invocation fail.
    """
    import json

    context = json.dumps({"cwd": cwd, "script": script, "code": code, "args": args})
    return (
        "import json as _wasm_json\n"
        "import os as _wasm_os, sys as _wasm_sys\n"
        f"_wasm_ctx = _wasm_json.loads({json.dumps(context)})\n"
        "_wasm_saved_argv, _wasm_saved_cwd = _wasm_sys.argv, _wasm_os.getcwd()\n"
        "_wasm_sys.argv = [_wasm_ctx['script'] or '-c', *_wasm_ctx['args']]\n"
        "_wasm_os.chdir(_wasm_ctx['cwd'])\n"
        "try:\n"
        "    if _wasm_ctx['script'] is not None:\n"
        "        with open(_wasm_ctx['script'], encoding='utf-8') as _wasm_fh:\n"
        "            _wasm_source = _wasm_fh.read()\n"
        "        _wasm_compiled = compile(_wasm_source, _wasm_ctx['script'], 'exec', dont_inherit=True)\n"
        "    else:\n"
        "        _wasm_compiled = compile(_wasm_ctx['code'], '<string>', 'exec', dont_inherit=True)\n"
        "    _wasm_namespace = {'__name__': '__main__', '__file__': _wasm_ctx['script'] or '<string>'}\n"
        "    try:\n"
        "        exec(_wasm_compiled, _wasm_namespace)\n"
        "    except SystemExit as _wasm_exit:\n"
        "        if _wasm_exit.code not in (None, 0):\n"
        "            raise RuntimeError(f'SystemExit: {_wasm_exit.code}') from None\n"
        "finally:\n"
        "    _wasm_sys.argv = _wasm_saved_argv\n"
        "    _wasm_os.chdir(_wasm_saved_cwd)\n"
    )


class WasmSandboxClient(LifecycleSessionClientMixin, BaseSandboxClient):
    """Build :class:`WasmSandboxSession`\\ s from a profile's client block.

    Nothing to provision: both runtimes are wasm artifacts already shipped
    with this package (bashkit in-process; pyodide in a Node subprocess),
    and the workspace is a durable host directory — same noop-snapshot
    semantics as ``unix_local``.
    """

    backend_id = "wasm"

    def __init__(
        self,
        *,
        workspace_dir: str | None = None,
        git: Any = False,
        network: Any = None,
        node_path: str = "node",
        packages: Sequence[str] = (),
        instrumentation: Optional[object] = None,
        **bash_kwargs: Any,
    ) -> None:
        # Host directory backing the VFS workspace. The profile passes
        # ``workspace_dir: ${workspace_dir}``; the host path stays out of
        # the sandbox's own namespace (manifest.root is /workspace).
        self._workspace_dir = workspace_dir
        self._git = git
        self._network = network
        self._node_path = node_path
        self._packages = tuple(packages)
        self._instrumentation = instrumentation
        self._bash_kwargs = dict(bash_kwargs)

    def _make_session(self, manifest: Manifest) -> WasmSandboxSession:
        root = str(manifest.root or "").strip()
        if not root or not root.startswith("/"):
            raise ValueError(
                "wasm sandbox requires manifest.root to be an absolute VFS "
                "path (use `root: /workspace` in the profile)"
            )
        host_dir = (self._workspace_dir or "").strip()
        if not host_dir or not os.path.isabs(host_dir):
            raise ValueError(
                "wasm sandbox requires client.workspace_dir to be an absolute "
                "host directory (use `workspace_dir: ${workspace_dir}` in the "
                "profile)"
            )
        return WasmSandboxSession(
            root=root,
            host_dir=host_dir,
            manifest=manifest,
            git=self._git,
            network=self._network,
            node_path=self._node_path,
            packages=self._packages,
            **dict(self._bash_kwargs),
        )

    async def create(
        self,
        *,
        snapshot: Any = None,
        manifest: Manifest | None = None,
        options: WasmSandboxClientOptions | None = None,
    ) -> SandboxSession:
        _ = (snapshot, options)  # workspace is durable on host; no options yet
        inner = self._make_session(manifest or Manifest())
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        inner = self._make_session(state.manifest)
        # The host directory persists; skip a redundant manifest re-apply.
        inner._set_start_state_preserved(True)
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    async def delete(self, session: SandboxSession) -> SandboxSession:
        await session.aclose()
        return session

    def deserialize_session_state(self, payload: dict[str, object]) -> Any:
        return SandboxSessionState.model_validate(payload)
