"""Sandbox-backed training environments for GRPO (+ ECHO) agent training.

This is the general, task-agnostic counterpart to ``omniagents.core.eval`` 's
``EvalEnvironment``: it lets an agent's tool calls execute inside a per-rollout
Docker sandbox during RL training, reusing the existing omniagents sandbox
machinery (``build_sandbox_client`` / ``SandboxSession``) and the
``omniagents.workspace`` contextvar seam (the same one ``SandboxAgent`` relies
on at eval/inference time).

It plugs into TRL's ``environment_factory`` protocol: TRL creates one
environment instance per rollout slot, calls the **sync** ``reset(**row)`` once
per rollout, and turns each **public method** into a tool the policy can call.
Tool-result tokens become TRL's ``tool_mask`` -- which is exactly what the ECHO
world-model loss (:class:`omniagents.core.training.echo.EchoGRPOTrainer`,
``world_model_coeff > 0``) trains on.

Design notes:
* ``reset`` is the only public method here, so the base contributes **no
  tools**. Subclasses add tool methods (e.g. a ``bash`` method) -- "terminal",
  "coding", etc. are *examples*, not framework concepts.
* **Isolation:** every rollout gets a *pristine, never-reused* container (torn
  down afterwards). That isolation is what keeps the verifier reward honest --
  reusing a dirty container would leak filesystem/processes/installed packages
  across tasks and silently corrupt the reward signal.
* **Warm pool:** creating+starting a container costs seconds, so a shared
  background :class:`_SandboxPool` keeps a pool of fresh containers ready and
  refills it concurrently off the critical path. ``reset`` then just grabs a
  ready container (sub-second) and tears the old one down asynchronously. All
  sandbox I/O runs on the pool's single dedicated event loop, so there are no
  cross-loop issues.
* Reward comes from a per-task **verifier command** run in the final container
  (exit code 0 = pass), via :func:`sandbox_verifier_reward`.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import threading
from collections import defaultdict, deque
from typing import Any, Callable, Optional

from omniagents.core.sandbox import build_sandbox_client
from omniagents.workspace._runtime import set_workspace
from omniagents.workspace.sandbox import SandboxWorkspace


# --------------------------------------------------------------------------- #
# Shared warm pool of fresh containers
# --------------------------------------------------------------------------- #
class _SandboxPool:
    """Background pool of pre-created, FRESH sandbox containers (per image).

    One dedicated event loop (daemon thread) runs all sandbox I/O: create, exec,
    delete. Per image a deque of ready, never-used sessions is kept topped up to
    ``pool_size`` by a background refill that creates up to
    ``max_concurrent_builds`` containers in flight. ``acquire`` hands out a
    pristine container; ``release`` tears one down asynchronously. The slow
    create latency is thus paid off the critical path while preserving one
    fresh container per rollout.
    """

    def __init__(
        self,
        sandbox_config: Optional[dict],
        *,
        pool_size: int,
        max_concurrent_builds: int,
        prewarm_image: str,
    ) -> None:
        self._sandbox_config = sandbox_config
        self._pool_size = pool_size
        self._max_builds = max_concurrent_builds
        self._client, _ = build_sandbox_client(self._config_for(prewarm_image))
        if self._client is None:
            raise ValueError("build_sandbox_client returned no client")
        self._ready: dict[str, deque] = defaultdict(deque)
        self._options_cache: dict[str, Any] = {}
        self._refilling: set[str] = set()

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        # Container create/delete are BLOCKING docker calls; run them on worker
        # threads so they happen in parallel (not serialized on the pool loop)
        # and never block exec/refill. (exec already offloads to an executor.)
        self._build_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_concurrent_builds, thread_name_prefix="sandbox-build"
        )
        atexit.register(self.close)
        self.warm(prewarm_image)

    def _config_for(self, image: str) -> dict:
        if self._sandbox_config is not None:
            return self._sandbox_config
        return {"client": {"type": "docker"}, "options": {"image": image}}

    def _options_for(self, image: str) -> Any:
        if image not in self._options_cache:
            _, opt = build_sandbox_client(self._config_for(image))
            self._options_cache[image] = opt
        return self._options_cache[image]

    def _run(self, coro):
        """Block until ``coro`` completes on the pool loop (call from other threads)."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    # ---- creation / refill ----
    def _blocking_create(self, options):
        # Runs on a build worker thread (own short-lived loop). The session is
        # then used on the pool loop -- sessions are loop-portable (docker ops
        # are sync-in-executor).
        return asyncio.run(self._client.create(options=options))

    async def _acreate(self, image: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._build_pool, self._blocking_create, self._options_for(image)
        )

    async def _arefill(self, image: str) -> None:
        # Single-threaded loop: this check/add is atomic (no await before add).
        if image in self._refilling:
            return
        self._refilling.add(image)
        try:
            deficit = self._pool_size - len(self._ready[image])
            tasks = [
                asyncio.create_task(self._acreate(image))
                for _ in range(max(0, deficit))
            ]
            for t in tasks:
                try:
                    self._ready[image].append(await t)
                except Exception:
                    pass
        finally:
            self._refilling.discard(image)

    async def _aacquire(self, image: str):
        session = (
            self._ready[image].popleft()
            if self._ready[image]
            else await self._acreate(image)
        )
        asyncio.create_task(
            self._arefill(image)
        )  # top the pool back up in the background
        return session

    async def _adelete(self, session) -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._build_pool, lambda: asyncio.run(self._client.delete(session))
            )
        except Exception:
            pass

    # ---- public (sync) API ----
    def warm(self, image: str) -> None:
        """Kick off background pre-warming for an image (fire-and-forget)."""
        asyncio.run_coroutine_threadsafe(self._arefill(image), self._loop)

    def acquire(self, image: str):
        return self._run(self._aacquire(image))

    def release(self, session) -> None:
        """Tear a container down asynchronously (does not block)."""
        if session is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self._adelete(session), self._loop)
        fut.add_done_callback(lambda f: f.exception())  # swallow teardown errors

    def run(self, coro):
        """Run an arbitrary sandbox coroutine on the pool loop and wait."""
        return self._run(coro)

    def submit(self, coro):
        """Schedule a coroutine on the pool loop; return its concurrent Future.

        Used to run sandbox work for many rollouts *concurrently* (await several
        with ``asyncio.wrap_future`` from another loop, or collect ``.result()``).
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def close(self) -> None:
        try:
            self._run(self._adrain())
        except Exception:
            pass
        self._build_pool.shutdown(wait=False, cancel_futures=True)
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _adrain(self) -> None:
        for dq in self._ready.values():
            while dq:
                try:
                    await self._client.delete(dq.popleft())
                except Exception:
                    pass


_POOLS: dict[str, _SandboxPool] = {}


def _get_pool(
    sandbox_config, pool_size, max_concurrent_builds, prewarm_image
) -> _SandboxPool:
    key = (
        json.dumps(sandbox_config or {"docker": True}, sort_keys=True)
        + f"|{pool_size}|{max_concurrent_builds}"
    )
    if key not in _POOLS:
        _POOLS[key] = _SandboxPool(
            sandbox_config,
            pool_size=pool_size,
            max_concurrent_builds=max_concurrent_builds,
            prewarm_image=prewarm_image,
        )
    else:
        _POOLS[key].warm(prewarm_image)  # ensure this image is being pre-warmed too
    return _POOLS[key]


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
class SandboxTrainingEnvironment:
    """Per-rollout Docker sandbox for TRL GRPO training (backed by a warm pool).

    One instance per rollout slot. ``reset`` releases the previous (used)
    container for async teardown and acquires a fresh one from the shared pool.
    Subclass it and add tool methods that drive the sandbox via
    ``omniagents.workspace.*``.

    Args:
        image: default Docker image (tasks may override per-row via ``docker_image``).
        sandbox_config: optional override for ``build_sandbox_client`` config.
        workspace_root: root the workspace seam resolves paths against.
        run_as: default user for sandboxed exec (None = image default).
        exec_timeout / verifier_timeout: timeouts for tool calls / the verifier.
        pool_size: warm containers kept ready per image (set >= your
            generation batch size to fully hide create latency).
        max_concurrent_builds: max container creations in flight while refilling.
    """

    def __init__(
        self,
        *,
        image: str,
        sandbox_config: Optional[dict] = None,
        workspace_root: str = "/",
        run_as: Optional[str] = None,
        exec_timeout: float = 30.0,
        verifier_timeout: float = 120.0,
        pool_size: int = 8,
        max_concurrent_builds: int = 12,
    ) -> None:
        self._default_image = image
        self._workspace_root = workspace_root
        self._run_as = run_as
        self._exec_timeout = exec_timeout
        self._verifier_timeout = verifier_timeout
        self._pool = _get_pool(sandbox_config, pool_size, max_concurrent_builds, image)
        self._session: Any = None
        self._workspace: Optional[SandboxWorkspace] = None
        self._task: dict = {}

    # ---- async bridge (all sandbox I/O runs on the shared pool loop) ----
    def _run(self, coro):
        return self._pool.run(coro)

    def _bind_workspace(self) -> None:
        """Bind the omniagents workspace contextvar to this rollout's session.

        Call at the top of a tool coroutine so ``omniagents.workspace.*`` calls
        in the tool body route into *this* container (contextvars are task-scoped).
        """
        if self._workspace is not None:
            set_workspace(self._workspace)

    # ---- TRL environment protocol ----
    def reset(self, *, prompt: Any = None, **task_fields: Any) -> Optional[str]:
        self._task = dict(task_fields)
        return self._pool.run(self._areset())

    async def _areset(self) -> Optional[str]:
        if self._session is not None:
            asyncio.create_task(
                self._pool._adelete(self._session)
            )  # async teardown of the used container
            self._session = None
        # "docker_image" preferred; "image" collides with TRL's multimodal column detection.
        image = (
            self._task.get("docker_image")
            or self._task.get("image")
            or self._default_image
        )
        self._session = await self._pool._aacquire(
            image
        )  # a pristine, never-used container
        self._workspace = SandboxWorkspace(
            session=self._session,
            root=self._workspace_root,
            default_user=self._run_as,
        )
        set_workspace(self._workspace)
        for cmd in self._task.get("setup") or []:
            await self._session.exec(
                cmd, shell=True, timeout=self._exec_timeout, user=self._run_as
            )
        return None

    # ---- verifier (underscored so TRL does not expose it as a tool) ----
    def _run_verifier(self) -> float:
        # Verifier-free ("world_model_only") tasks return a constant: the group
        # then has zero advantage (no policy gradient) but ECHO still trains on
        # their environment tokens (verifier-free adaptation).
        if self._task.get("world_model_only"):
            return 0.0
        verifier = self._task.get("verifier")
        if not verifier or self._session is None:
            return 0.0
        return self._pool.run(self._averify(verifier))

    async def _averify(self, verifier: str) -> float:
        try:
            result = await self._session.exec(
                verifier, shell=True, timeout=self._verifier_timeout, user=self._run_as
            )
        except Exception:
            return 0.0
        return 1.0 if result.exit_code == 0 else 0.0

    def _close(self) -> None:
        if self._session is not None:
            self._pool.release(self._session)
            self._session = None


def make_environment_factory(
    env_cls: type[SandboxTrainingEnvironment] = SandboxTrainingEnvironment,
    **kwargs: Any,
) -> Callable[[], SandboxTrainingEnvironment]:
    """Build the zero-arg factory TRL needs (one env per rollout slot)."""
    return lambda: env_cls(**kwargs)


def sandbox_verifier_reward(
    *,
    prompts: Any = None,
    completions: Any = None,
    completion_ids: Any = None,
    environments: Optional[list] = None,
    **kwargs: Any,
) -> list[float]:
    """TRL reward function: run each rollout's verifier in its final container.

    TRL passes ``environments`` aligned 1:1 with ``completions``. Returns 1.0
    for a passing verifier (exit code 0), else 0.0; robust to missing
    environments and per-env errors.
    """
    n = len(completions) if completions is not None else (len(environments or []))
    if not environments:
        return [0.0] * n

    # Run every rollout's verifier CONCURRENTLY on the shared pool loop (they
    # each offload the container exec to the pool's thread executor), instead of
    # one-after-another. Big win when verifiers are non-trivial (e.g. pytest).
    futs = []
    for env in environments:
        verifier = env._task.get("verifier")
        if env._task.get("world_model_only") or not verifier or env._session is None:
            futs.append(None)
        else:
            futs.append(env._pool.submit(env._averify(verifier)))
    rewards: list[float] = []
    for f in futs:
        if f is None:
            rewards.append(0.0)
            continue
        try:
            rewards.append(float(f.result()))
        except Exception:
            rewards.append(0.0)
    return rewards


sandbox_verifier_reward.__name__ = "sandbox_verifier"
