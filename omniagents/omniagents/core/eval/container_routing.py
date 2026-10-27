"""Container routing for evaluation environments.

``ContainerRoutingMixin`` gives any :class:`EvalEnvironment` the
"environment in container" shape: the agent's model loop runs **on the
host** in the standard in-process eval pipeline, and its environment -
filesystem, shell, processes - is a container. The container is registered
as the scenario's execution environment
(:meth:`register_environment`), exactly as ``omni serve`` registers a
profile's sandbox, so the service routes every run there: file tools go
through a ``SandboxWorkspace`` and shell tools through the SDK sandbox
session, while the model client and all measures stay host-side.

The container shape comes entirely from a **sandbox profile**
(:mod:`omniagents.core.sandbox.profiles`); the mixin carries no default
image, no default profile, and no resolver — the composing product
supplies :attr:`profile_resolver` (and optionally
:attr:`default_profile_path` for the bare ``environment.image``
shorthand). A scenario opts in by naming one::

    scenarios:
      - name: refuses_rm_rf_root
        fixture: python_fastapi
        environment:
          profile: evaluations/profiles/devbox.yml   # container shape, pinned

The resolver is called with ``explicit_path``, which merges only the
product's built-in default + the named file and skips the user/project
layers — hermetic and reproducible. ``environment.image`` is supported
as a quick override (it just overwrites ``options.image`` of the
resolved profile). Scenarios with neither ``profile`` nor ``image``
(and no class-level ``default_profile``) fall through to the composing
environment's behavior unchanged, so a mixed class is safe as the global
eval environment.

Compose it BEFORE the workspace environment in the bases list — the
mixin's overrides must win the MRO, and each falls through to the
workspace environment via ``super()`` for host-only scenarios::

    class DevboxEvalEnvironment(ContainerRoutingMixin, WorkspaceEvalEnvironment):
        profile_resolver = staticmethod(resolve_profile)
        default_profile_path = MY_DEFAULT_EVAL_PROFILE

Lifted from omni-code.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import shlex
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Optional

_logger = logging.getLogger(__name__)

__all__ = ["ContainerRoutingMixin"]


def _run_coro_sync(coro: Any) -> Any:
    """Run an async coroutine from sync code, whether or not a loop is running.

    Measures are sync functions but may be invoked while the eval pipeline's
    event loop is running; in that case we can't ``asyncio.run`` directly, so
    we hand the coroutine to a worker thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _decode(b: Any) -> str:
    if isinstance(b, (bytes, bytearray)):
        return b.decode("utf-8", "replace")
    return b or ""


class ContainerRoutingMixin:
    """Route an eval environment's shell/file surface into a container.

    Product knobs (set on the composing class):

    - ``profile_resolver``: callable with the signature of
      :func:`omniagents.core.sandbox.profiles.resolve_profile` already
      bound to the product's config dir + packaged default (products
      typically assign their own ``resolve_profile`` binding). Required
      for any scenario that requests a container.
    - ``default_profile_path``: profile used when a scenario sets
      ``environment.image`` without naming a profile. Optional; without
      it, bare ``image`` scenarios are an error.
    - ``CONTAINER_WORKSPACE_ROOT``: container manifest root the staged
      workspace is mounted at (default ``/workspace``; a mount-point
      convention, not an image assumption).
    """

    #: Container manifest root. The fixture is materialized here (entry key
    #: "." → root) and the agent's shell/fs tools are anchored here.
    CONTAINER_WORKSPACE_ROOT: ClassVar[str] = "/workspace"

    # Not ClassVar: subclasses may set these per-class OR per-instance
    # (ConfiguredWorkspaceEnvironment wires them in __init__).
    profile_resolver: Optional[Callable[..., Any]] = None
    default_profile_path: Optional[Path] = None
    profile_base_dir: Optional[Path] = None

    # Live sessions keyed by session_id. Kept off EnvironmentContext.data
    # because the runner merges context.data into spec.context (tool
    # context) — a live SandboxSession does not belong there.
    @property
    def _sessions(self) -> Dict[str, Dict[str, Any]]:
        store = getattr(self, "_container_sessions", None)
        if store is None:
            store = {}
            self._container_sessions = store
        return store

    # ── lifecycle ───────────────────────────────────────────────────

    async def setup(self, scenario: Dict[str, Any]) -> Any:
        # Host staging first (the composing workspace environment).
        context = await super().setup(scenario)
        profile_path, image_override = self._requested_container(scenario)
        await self.prepare_runtime(
            context, scenario, in_container=profile_path is not None
        )
        if profile_path is None:
            # No container requested — behave exactly like the parent.
            await self.runtime_ready(context, scenario)
            return context

        if self.profile_resolver is None:
            raise RuntimeError(
                "Scenario requests a container but no profile_resolver is "
                "configured on the environment (set profile_resolver on the "
                "class composing ContainerRoutingMixin)."
            )

        tmp_dir = context.get("workspace_root")

        from agents.sandbox.manifest import Environment

        from omniagents.core.sandbox.factory import (
            build_manifest,
            build_sandbox_client,
        )

        # Resolve the container shape through the profile system (hermetic via
        # explicit_path: the product's built-in default + this file only).
        profile = self.profile_resolver(
            workspace_dir=Path(tmp_dir), explicit_path=profile_path
        )
        # Quick image override (ad-hoc pin without authoring a profile file),
        # applied before the build so `build:` steps land on that image.
        if image_override:
            raw = copy.deepcopy(profile.raw)
            raw.setdefault("options", {})["image"] = image_override
            profile = dataclasses.replace(profile, raw=raw)
        # The profile's `build:` steps are built into its image once, not
        # rerun in every scenario's container.
        from omniagents.core.sandbox.images import prepare_profile_image

        profile = await prepare_profile_image(profile)
        raw = copy.deepcopy(profile.raw)

        # Workspace is a per-scenario concern, not part of the profile: pin the
        # manifest root and mount the staged fixture there (entry key "." ==
        # path-under-root == the root itself, so the fixture lands at the
        # container root rather than a nested subdir). Other manifest keys the
        # profile set (e.g. environment / network allowlist) are preserved.
        manifest_block = raw.setdefault("manifest", {})
        manifest_block["root"] = self.CONTAINER_WORKSPACE_ROOT
        manifest_block.setdefault("entries", {})["."] = {
            "type": "local_dir",
            "src": tmp_dir,
        }

        client, options = build_sandbox_client(raw)
        manifest = build_manifest(raw)

        # The scenario's environment variables reach the agents' processes
        # in the container, as the profile's own do.
        manifest = manifest.model_copy(
            update={
                "environment": Environment(
                    value={
                        **manifest.environment.value,
                        **self.container_env_vars(context),
                    }
                )
            }
        )

        # Pre-declare the run_as user on the manifest so the SDK's per-run User
        # injection validates. Skipped when the profile sets no run_as (the
        # container then defaults to root, fine for a throwaway eval container).
        run_as = raw.get("run_as")
        if isinstance(run_as, str) and run_as:
            from agents.sandbox.manifest import User

            manifest = manifest.model_copy(
                update={"users": [*manifest.users, User(name=run_as)]},
                deep=True,
            )

        session = await client.create(manifest=manifest, options=options, snapshot=None)
        # Recorded before start, so teardown stops a container whose start or
        # init failed.
        self._sessions[context.session_id] = {
            "session": session,
            "run_as": run_as if isinstance(run_as, str) and run_as else None,
            "profile_raw": raw,
        }
        await session.start()

        # Run the profile's init hooks (user remap, daemon init, ...)
        # sequentially, aborting on the first failure.
        for cmd in profile.init:
            res = await session.exec(cmd)
            code = getattr(res, "exit_code", 0)
            if code not in (0, None):
                err = getattr(res, "stderr", b"")
                raise RuntimeError(
                    f"profile init step failed ({cmd!r}, exit {code}): {err!r}"
                )

        _logger.info(
            "%s: container %s up for scenario %r (profile=%s, image=%s, run_as=%s)",
            type(self).__name__,
            str(getattr(session.state, "container_id", "?"))[:12],
            scenario.get("name"),
            profile_path.name,
            (raw.get("options") or {}).get("image"),
            run_as,
        )
        await self.runtime_ready(context, scenario)
        return context

    def _requested_container(
        self, scenario: Dict[str, Any]
    ) -> tuple[Optional[Path], Optional[str]]:
        """The profile (and image override) a scenario's container uses, if any."""
        env_block = scenario.get("environment") or {}
        profile_ref = env_block.get("profile") or getattr(self, "default_profile", None)
        image_override = env_block.get("image") or getattr(self, "default_image", None)
        if profile_ref:
            return self._resolve_profile_path(profile_ref), image_override
        if not image_override:
            return None, None
        if self.default_profile_path is None:
            raise RuntimeError(
                "Scenario sets environment.image without a profile, but "
                "no default_profile_path is configured on the environment."
            )
        return Path(self.default_profile_path), image_override

    async def prepare_runtime(
        self, context: Any, scenario: Dict[str, Any], *, in_container: bool
    ) -> None:
        """Hook: before the scenario's runtime exists; a container is
        created with :meth:`container_env_vars` as they stand after it."""

    async def runtime_ready(self, context: Any, scenario: Dict[str, Any]) -> None:
        """Hook: the scenario's runtime (its container, or the host) is up."""

    def container_session(self, context: Any) -> Optional[Dict[str, Any]]:
        """The scenario's live container (``session``, ``run_as``), if any."""
        return self._sessions.get(context.session_id) if context else None

    def container_env_vars(self, context: Any) -> Dict[str, str]:
        """Environment variables the container's processes get.

        Default: :meth:`get_env_vars`, with workspace-shaped values pointed
        at the container's workspace.
        """
        env = dict(super().get_env_vars(context))
        host_root = context.get("workspace_root", "")
        for key, value in list(env.items()):
            if value == host_root:
                env[key] = self.CONTAINER_WORKSPACE_ROOT
        return env

    async def register_environment(self, resources: Any, context: Any) -> Any:
        record = self.container_session(context)
        if not record:
            return await super().register_environment(resources, context)
        from omniagents.core.runtime.environment_provisioner import (
            sandbox_capabilities,
        )
        from omniagents.execution import register_sandbox_environment

        # The capabilities production gives this profile's sandbox
        # (``contained`` for a docker container), so the approval policy
        # treats the scenario's container as it would the real one.
        return register_sandbox_environment(
            resources,
            record["session"],
            self.CONTAINER_WORKSPACE_ROOT,
            owner_user_id=None,
            capabilities=sandbox_capabilities(record["profile_raw"]),
            run_as=record.get("run_as"),
        )

    def run_command(self, context: Any, command: str, timeout: int = 120) -> tuple:
        record = self._sessions.get(context.session_id) if context else None
        if not record:
            # Host-only scenario — defer to the composing environment.
            return super().run_command(context, command, timeout)
        session = record["session"]

        async def _exec() -> Any:
            return await asyncio.wait_for(session.exec(command), timeout)

        try:
            res = _run_coro_sync(_exec())
        except asyncio.TimeoutError:
            return (124, "", f"command timed out after {timeout}s")
        code = getattr(res, "exit_code", None)
        return (
            0 if code is None else int(code),
            _decode(getattr(res, "stdout", b"")),
            _decode(getattr(res, "stderr", b"")),
        )

    # The verify() matching logic is the EvalEnvironment base's; only the two
    # file primitives differ — files live inside the container, reached via
    # the live SandboxSession. Host-only scenarios (no session) fall through
    # to the composing environment's primitives.

    async def _path_exists(self, context: Any, rel_path: str) -> bool:
        record = self._sessions.get(context.session_id) if context else None
        if not record:
            return await super()._path_exists(context, rel_path)
        qpath = shlex.quote(f"{self.CONTAINER_WORKSPACE_ROOT}/{rel_path}")
        r = await record["session"].exec(f"test -e {qpath}")
        return getattr(r, "exit_code", 1) == 0

    async def _read_file(self, context: Any, rel_path: str) -> Optional[str]:
        record = self._sessions.get(context.session_id) if context else None
        if not record:
            return await super()._read_file(context, rel_path)
        qpath = shlex.quote(f"{self.CONTAINER_WORKSPACE_ROOT}/{rel_path}")
        cr = await record["session"].exec(f"cat {qpath}")
        if getattr(cr, "exit_code", 1) != 0:
            return None
        return _decode(getattr(cr, "stdout", b""))

    async def teardown(self, context: Any) -> None:
        record = self._sessions.pop(context.session_id, None) if context else None
        if record is not None:
            session = record["session"]
            try:
                await session.stop()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                _logger.warning("%s: session.stop failed: %s", type(self).__name__, exc)
            try:
                await session.aclose()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "%s: session.aclose failed: %s", type(self).__name__, exc
                )
        # Host cleanup (the composing workspace environment).
        await super().teardown(context)

    # ── helpers ─────────────────────────────────────────────────────

    def _resolve_profile_path(self, ref: str | Path) -> Path:
        """Resolve a scenario's ``environment.profile`` to a path.

        Absolute paths are honored; relative paths resolve against
        :attr:`profile_base_dir` (the evaluation directory, for the
        declarative environment), else the invocation's cwd.
        """
        p = Path(ref).expanduser()
        if p.is_absolute():
            return p
        base = getattr(self, "profile_base_dir", None)
        return (Path(base) if base else Path.cwd()) / p
