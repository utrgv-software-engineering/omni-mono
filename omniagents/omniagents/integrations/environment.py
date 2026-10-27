"""Factory sandboxes: disposable per run, or persistent for personal mode.

Warp runs every agent in its runner's sandbox with the factory's
repositories in reach. Here each automation run gets a fresh sandbox from
the project's sandbox profile (``paths.sandbox_profile``, else
``sandbox_profile_default.yml`` beside ``project.yml``, under the product's
user layers such as ``<config>/sandbox/default.yml``), brought up the way
``omni serve`` brings up a profile's sandbox, with the github integration's
repositories cloned at ``/workspace/<name>``. The sandbox is removed when
the run ends; work that outlives a run lives in branches and pull requests.

``project.yml``::

    integrations:
      - type: github
        repositories: [acme/api-service, acme/webapp]
        git_url: ${GITHUB_GIT_URL:-}     # where github.com's git is served instead

``git_url`` points the sandbox's git at another server (the workplace
simulator) for ``https://github.com/``: the clones and every later fetch and
push go there, while the repositories keep their GitHub remotes, and git
sends the token to it (as an ``Authorization`` header for that URL only, as
GitHub's checkout action does). The token is ``GH_TOKEN`` (``token_env``,
read when a sandbox comes up), which also authenticates git and ``gh`` for
github.com itself.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

__all__ = ["GitHubConfig", "RunEnvironments", "RunSandbox", "parse_github"]

logger = logging.getLogger(__name__)

GITHUB = "https://github.com/"


@dataclass(frozen=True)
class GitHubConfig:
    """``integrations: [{type: github, ...}]`` in ``project.yml``."""

    repositories: Tuple[str, ...] = ()
    git_url: Optional[str] = None
    token_env: str = "GH_TOKEN"
    #: The token itself, for a caller holding one (an evaluation's scenario);
    #: otherwise ``token_env``'s value.
    token: Optional[str] = None

    def resolved_token(self) -> Optional[str]:
        return self.token or os.environ.get(self.token_env) or None

    def sources(self) -> List[Dict[str, Any]]:
        """The repositories as sandbox sources, one at ``/workspace/<name>`` each."""
        return [
            {
                "kind": "git-remote",
                "repoUrl": f"{GITHUB}{repository}",
                "mountName": repository.rsplit("/", 1)[-1],
                # Sent to git_url as a header instead (git_environment).
                **({} if self.git_url else {"auth": {"tokenEnv": self.token_env}}),
            }
            for repository in self.repositories
        ]

    def git_environment(self) -> Dict[str, str]:
        """Git configuration, as environment, that sends github.com to ``git_url``
        with the token."""
        if not self.git_url:
            return {}
        base = self.git_url.rstrip("/")
        config = [(f"url.{base}/.insteadOf", GITHUB)]
        token = self.resolved_token()
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            config.append(
                (f"http.{base}/.extraHeader", f"Authorization: Basic {basic}")
            )
        env = {"GIT_CONFIG_COUNT": str(len(config))}
        for n, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = key, value
        return env


def parse_github(entries: Any) -> Optional[GitHubConfig]:
    """The github integration in a ``project.yml`` ``integrations:`` list."""
    from omniagents.core.config.loader import _substitute_env_vars

    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("type") != "github":
            continue
        from omniagents.core.project.github_settings import github_settings

        entry = github_settings(entry)
        try:
            entry = _substitute_env_vars(dict(entry))
        except ValueError as exc:
            logger.warning("integrations: github is off: %s", exc)
            return None
        repositories = entry.get("repositories") or []
        return GitHubConfig(
            repositories=tuple(str(r) for r in repositories),
            git_url=entry.get("git_url") or None,
            token_env=str(entry.get("token_env") or "GH_TOKEN"),
        )
    return None


@dataclass
class RunSandbox:
    """A sandbox brought up for one run: its session, and where its workspace is."""

    session: Any
    root: str
    capabilities: frozenset
    run_as: Optional[str]
    container_id: Optional[str]

    def register(self, resources: Any, *, owner_user_id: str | None = None) -> Any:
        """Make it ``resources``' default environment, so runs on them work in it."""
        from omniagents.execution import register_sandbox_environment

        descriptor = register_sandbox_environment(
            resources,
            self.session,
            self.root,
            owner_user_id=owner_user_id,
            capabilities=self.capabilities,
            run_as=self.run_as,
        )
        resources.thread_bindings.set_initial_default(descriptor)
        return descriptor


class RunEnvironments:
    """Project sandboxes; a saved state path opts into personal persistence."""

    def __init__(self, profile_path: Path, github: Optional[GitHubConfig]) -> None:
        self.profile_path = Path(profile_path)
        self.github = github or GitHubConfig()

    @asynccontextmanager
    async def sandbox(
        self, label: str = "automation", *, state_path: Optional[Path] = None
    ) -> AsyncIterator[RunSandbox]:
        """Bring up repositories; retain the container when state_path is supplied."""
        from omniagents.core.runtime.environment_provisioner import (
            sandbox_capabilities,
        )
        from omniagents.core.runtime.serve_harness import (
            _run_as_exec_user,
            bring_up_session,
            teardown_session,
        )
        from omniagents.core.sandbox.profiles import (
            _manifest_env_values,
            resolve_profile,
        )

        run_id = f"{label}-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix=f"omni-{label}-") as scratch:
            # The project's profile under the product's user layers
            # (<config>/sandbox/default.yml): where a deployment says which
            # network sandboxes join.
            profile = resolve_profile(
                workspace_dir=Path(scratch), default_profile_path=self.profile_path
            )
            if state_path and (profile.raw.get("client") or {}).get("type") != "docker":
                raise ValueError(
                    "The persistent personal workspace requires a Docker profile"
                )
            git_environment = self.github.git_environment()
            library_root = os.environ.get("LIBRARY_FILES_HOST_PATH")
            if git_environment or library_root:
                raw = copy.deepcopy(profile.raw)
                values = _manifest_env_values(raw)
                if values is not None:
                    values.update(git_environment)
                if library_root:
                    if not os.path.isabs(library_root):
                        raise ValueError(
                            "LIBRARY_FILES_HOST_PATH must be an absolute host directory"
                        )
                    client = raw.setdefault("client", {})
                    binds = client.setdefault("binds", [])
                    if not any(b.get("target") == "/workspace/Library" for b in binds):
                        binds.append(
                            {"source": library_root, "target": "/workspace/Library"}
                        )
                profile = dataclasses.replace(profile, raw=raw)

            async def prepare_persistent(session):
                from .personal_workspace import refresh_environment

                await refresh_environment(
                    session, self.github, run_as=_run_as_exec_user(profile.raw)
                )

            sources = self.github.sources()
            brought = await bring_up_session(
                profile=profile,
                sources_parsed=sources,
                credentials_parsed=[
                    {"url": GITHUB.rstrip("/"), "tokenEnv": self.github.token_env}
                ],
                snapshot_ref=run_id,
                snapshot_dir=None,
                state_path=state_path,
                durable=state_path is not None,
                prepare_session=prepare_persistent if state_path else None,
            )
            logger.info(
                "integrations: sandbox %s up for a %s run (%s)",
                str(brought.container_id or "?")[:12],
                label,
                ", ".join(s["mountName"] for s in sources) or "no repositories",
            )
            try:
                yield RunSandbox(
                    session=brought.session,
                    root=str(getattr(brought.manifest, "root", None) or "/workspace"),
                    capabilities=sandbox_capabilities(profile.raw, brought),
                    run_as=_run_as_exec_user(profile.raw),
                    container_id=brought.container_id,
                )
            finally:
                await teardown_session(
                    brought.session,
                    brought.started_services,
                    detach=state_path is not None,
                    destroy=state_path is None,
                )

    @asynccontextmanager
    async def open(self) -> AsyncIterator[Any]:
        """A sandbox, as the AgentHost resources that route a run into it."""
        from omniagents.execution import AgentHostResources

        async with self.sandbox() as sandbox:
            resources = AgentHostResources.create(f"automation-{uuid.uuid4().hex[:12]}")
            sandbox.register(resources)
            yield resources
