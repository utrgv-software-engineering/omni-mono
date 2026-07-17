"""Sandbox profile resolution for omni-code.

A thin product binding over the framework's layered profile resolver
(``omniagents.core.sandbox.profiles``): the framework owns the merge
chain, ``${workspace_dir}`` substitution, network-allowlist / user-.env
injection, and the ``init``/``services``/``terminal`` extension. This
module supplies omni-code's config directory and packaged default
profile.

Resolution order (last wins on key conflict, dicts merge, lists replace):

    1. omni-code built-in default (this package)
    2. <config>/sandbox/default.yml          (launcher writes from global settings)
    3. <config>/projects/<projectId>/sandbox.yml   (launcher writes per-project)
    4. --profile <path>                      (explicit CLI override; tests, scripting)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from omniagents.core.sandbox.profiles import (
    ResolvedProfile,
    ServiceSpec,
    TerminalSpec,
    resolve_profile as _framework_resolve_profile,
)

from omni_code.config import get_config_dir

__all__ = ["ResolvedProfile", "ServiceSpec", "TerminalSpec", "resolve_profile"]

_DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "sandbox_profile_default.yml"


def resolve_profile(
    *,
    workspace_dir: Path,
    project_id: Optional[str] = None,
    explicit_path: Optional[Path] = None,
) -> ResolvedProfile:
    """Resolve a sandbox profile through omni-code's layered chain."""
    return _framework_resolve_profile(
        workspace_dir=workspace_dir,
        config_dir=get_config_dir(),
        default_profile_path=_DEFAULT_PROFILE_PATH,
        project_id=project_id,
        explicit_path=explicit_path,
    )
