"""Environment-in-container evaluation environment.

A composition of two independent concerns:

- ``WorkspaceEvalEnvironment`` (what gets staged): host tempdir, fixture
  copy respecting .gitignore, git init, inline AGENTS.md/skills, the
  ``db_fixture`` MCP wiring, workspace process cleanup.
- The framework's ``ContainerRoutingMixin`` (where tools execute): when a
  scenario names a sandbox profile (or an image), the container comes up,
  the staged workspace is mounted into it, and the agent's shell/file
  tools exec inside it via the SDK ``SandboxAgent`` path — while the LLM
  loop, model client, and all measures stay host-side.

omni-code supplies only the product knobs: its profile resolver (the
same layered YAML system ``omni serve`` uses, called hermetically with
``explicit_path``) and the packaged default eval profile used by the
bare ``environment.image`` shorthand.

Scenarios with neither ``profile`` nor ``image`` fall through to plain
host-tempdir behavior unchanged, so this class is safe as the global
eval environment in ``evaluation.yml``::

    scenarios:
      - name: refuses_rm_rf_root
        fixture: python_fastapi
        environment:
          profile: evaluations/profiles/devbox.yml   # container shape, pinned
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omniagents.core.eval.container_routing import ContainerRoutingMixin

from omni_code.sandbox_profile import resolve_profile

from .workspace_env import WorkspaceEvalEnvironment

# Packaged default profile, used when a scenario sets `image` without a
# profile. A real YAML resolved through the profile system — not a
# hand-built dict — so it composes with the built-in default like any
# other profile.
_DEFAULT_EVAL_PROFILE = (
    Path(__file__).resolve().parent.parent
    / "evaluations"
    / "profiles"
    / "devbox.yml"
)


class DevboxEvalEnvironment(ContainerRoutingMixin, WorkspaceEvalEnvironment):
    """Host-side agent + per-scenario containerized environment."""

    profile_resolver = staticmethod(resolve_profile)
    default_profile_path = _DEFAULT_EVAL_PROFILE

    def __init__(
        self,
        profile: str | None = None,
        image: str | None = None,
        fixtures_dir: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(fixtures_dir=fixtures_dir, **kwargs)
        # Optional class-level fallbacks; per-scenario `environment:` wins.
        # Normally both are None so container mode is strictly per-scenario.
        self.default_profile = profile
        self.default_image = image
