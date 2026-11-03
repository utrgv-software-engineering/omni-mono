"""Factory sandboxes: kept across restarts when a state path is supplied.

The factory's shared machine is one (``server._attach_factory_workspace``);
evaluations open disposable ones. Persistent repository paths include a stable provider/repository hash, so
switching GitHub providers never reuses another provider's checkout.
``/workspace/.omni-repositories.json`` lists active paths and preparation
errors. Previous checkouts remain on disk, including uncommitted work.
Disposable callers keep the legacy ``/workspace/<name>`` layout.

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
import hashlib
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

    def sources(self, *, persistent: bool = False) -> List[Dict[str, Any]]:
        """Return sources; persistent paths distinguish provider and full repo name."""
        return [
            {
                "kind": "git-remote",
                "repoUrl": f"{GITHUB}{repository}",
                "mountName": (
                    repository.rsplit("/", 1)[-1]
                    + "--"
                    + hashlib.sha256(
                        (
                            (self.git_url or GITHUB).rstrip("/") + "/" + repository
                        ).encode()
                    ).hexdigest()[:16]
                    if persistent
                    else repository.rsplit("/", 1)[-1]
                ),
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
    """Project sandboxes; a saved state path keeps one across restarts."""

    def __init__(
        self,
        profile_path: Path,
        github: Optional[GitHubConfig],
        *,
        explicit_profile: bool = False,
        library_root: Optional[str] = None,
        shared_root: Optional[str] = None,
    ) -> None:
        self.library_root = library_root
        self.shared_root = shared_root
        self.explicit_profile = explicit_profile
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
                workspace_dir=Path(scratch),
                default_profile_path=self.profile_path,
                explicit_path=self.profile_path if self.explicit_profile else None,
            )
            if state_path and (profile.raw.get("client") or {}).get("type") not in {
                "docker",
                "quicksand",
            }:
                raise ValueError(
                    "The persistent factory workspace requires a Docker or QuickSand profile"
                )
            git_environment = self.github.git_environment()
            library_root = (
                self.library_root
                if self.library_root is not None
                else os.environ.get("LIBRARY_FILES_HOST_PATH")
            )
            shared_root = (
                self.shared_root
                if self.shared_root is not None
                else os.environ.get("SHARED_FILES_HOST_PATH")
            )
            if git_environment or library_root or shared_root:
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
                if shared_root:
                    if not os.path.isabs(shared_root):
                        raise ValueError(
                            "SHARED_FILES_HOST_PATH must be an absolute host directory"
                        )
                    binds = raw.setdefault("client", {}).setdefault("binds", [])
                    if not any(b.get("target") == "/workspace/Shared" for b in binds):
                        binds.append(
                            {"source": shared_root, "target": "/workspace/Shared"}
                        )
                profile = dataclasses.replace(profile, raw=raw)

            async def prepare_persistent(session):
                from .personal_workspace import refresh_environment

                await refresh_environment(
                    session, self.github, run_as=_run_as_exec_user(profile.raw)
                )

            sources = self.github.sources(persistent=state_path is not None)
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
