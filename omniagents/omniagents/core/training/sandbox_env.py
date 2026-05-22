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
* All sandbox I/O is async, but TRL calls ``reset`` synchronously and tools may
  be sync. Each instance owns a **dedicated asyncio loop in a daemon thread**;
  ``reset`` and tool methods bridge to it via ``run_coroutine_threadsafe``,
  keeping one rollout's container operations serialized and independent of
  TRL's own event loop.
* Reward comes from a per-task **verifier command** run in the final container
  (exit code 0 = pass), via :func:`sandbox_verifier_reward`. No Python verifier
  callable is needed, and no exec method is exposed as a tool.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Any, Callable, Optional

from omniagents.core.sandbox import build_sandbox_client
from omniagents.workspace._runtime import set_workspace
from omniagents.workspace.sandbox import SandboxWorkspace


class SandboxTrainingEnvironment:
    """Per-rollout Docker sandbox for TRL GRPO training.

    One instance per rollout slot (TRL calls the factory
    ``generation_batch_size`` times and reuses the instances, calling
    ``reset`` before each rollout). Subclass it and add tool methods that drive
    the sandbox via ``omniagents.workspace.*`` (see :meth:`_bind_workspace`).

    Args:
        image: Docker image for the sandbox (e.g. ``"python:3.11-slim"``).
        sandbox_config: Optional override for the ``build_sandbox_client``
            config; defaults to a local Docker client for ``image``.
        workspace_root: Root the workspace seam resolves paths against.
        run_as: Default user for sandboxed exec (``None`` = image default,
            which is root in stock images; set to a non-root user for defense
            in depth).
        exec_timeout: Default per-command timeout for tool calls.
        verifier_timeout: Timeout for the verifier command.
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
    ) -> None:
        self._default_image = image
        self._sandbox_config = sandbox_config
        self._workspace_root = workspace_root
        self._run_as = run_as
        self._exec_timeout = exec_timeout
        self._verifier_timeout = verifier_timeout

        # One client; options (which carry the image) are resolved per task so a
        # single environment instance can serve tasks with different images
        # (each task in a corpus may ship its own container).
        self._client, _ = build_sandbox_client(self._config_for(image))
        if self._client is None:
            raise ValueError(f"build_sandbox_client returned no client for image {image!r}")
        self._options_cache: dict[str, Any] = {}

        self._session: Any = None
        self._workspace: Optional[SandboxWorkspace] = None
        self._task: dict = {}

        # Dedicated background event loop for all sandbox I/O.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        atexit.register(self._close)

    # ---- async bridge ----
    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _bind_workspace(self) -> None:
        """Bind the omniagents workspace contextvar to this rollout's session.

        Call at the top of a tool coroutine so that ``omniagents.workspace.*``
        calls in the tool body route into *this* container. (contextvars are
        task-scoped, so each tool-call coroutine must bind it.)
        """
        if self._workspace is not None:
            set_workspace(self._workspace)

    # ---- TRL environment protocol ----
    def reset(self, *, prompt: Any = None, **task_fields: Any) -> Optional[str]:
        """Tear down the prior container, start a fresh one, run setup.

        Receives the dataset row as kwargs (``prompt`` plus the suite's expect
        columns, e.g. ``setup``/``verifier``). Returns an optional initial
        observation string TRL appends to the prompt (default ``None``).
        """
        self._task = dict(task_fields)
        return self._run(self._areset())

    def _config_for(self, image: str) -> dict:
        if self._sandbox_config is not None:
            return self._sandbox_config
        return {"client": {"type": "docker"}, "options": {"image": image}}

    def _options_for(self, image: str) -> Any:
        if image not in self._options_cache:
            _, options = build_sandbox_client(self._config_for(image))
            self._options_cache[image] = options
        return self._options_cache[image]

    async def _areset(self) -> Optional[str]:
        if self._session is not None:
            try:
                await self._client.delete(self._session)
            except Exception:
                pass
            self._session = None
        # "docker_image" preferred; "image" collides with TRL's multimodal
        # column detection, so callers should avoid naming a column "image".
        image = self._task.get("docker_image") or self._task.get("image") or self._default_image
        self._session = await self._client.create(options=self._options_for(image))
        self._workspace = SandboxWorkspace(
            session=self._session,
            root=self._workspace_root,
            default_user=self._run_as,
        )
        set_workspace(self._workspace)
        for cmd in self._task.get("setup") or []:
            await self._session.exec(cmd, shell=True, timeout=self._exec_timeout, user=self._run_as)
        return None

    # ---- verifier (called by sandbox_verifier_reward; underscored so TRL does
    # not expose it as a tool) ----
    def _run_verifier(self) -> float:
        # Verifier-free ("world_model_only") tasks return a constant reward: the
        # group then has zero advantage (no policy gradient), but the ECHO
        # auxiliary loss still trains on their environment tokens. This is the
        # mechanism behind verifier-free adaptation.
        if self._task.get("world_model_only"):
            return 0.0
        verifier = self._task.get("verifier")
        if not verifier or self._session is None:
            return 0.0
        return self._run(self._averify(verifier))

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
            try:
                self._run(self._client.delete(self._session))
            except Exception:
                pass
            self._session = None
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


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
    rewards: list[float] = []
    for env in environments:
        try:
            rewards.append(float(env._run_verifier()))
        except Exception:
            rewards.append(0.0)
    return rewards


sandbox_verifier_reward.__name__ = "sandbox_verifier"
