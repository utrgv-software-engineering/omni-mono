"""Lightweight evaluation environment using temporary workspaces.

A product subclass of the framework's ``LocalWorkspaceEnvironment``: the
tempdir staging, gitignore-respecting fixture copy, inline AGENTS.md /
skills, git init, process reaping, and keep-tempdir diagnostics all live
framework-side. This class adds omni-code's parts — the fixture-tree
default, product env vars, and MCP-backed project state (``db_fixture``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from omniagents.core.eval.environment import EnvironmentContext
from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment
from omniagents.core.eval.mcp_fixture import UserMcpFixtureStager

_DEFAULT_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "workspaces"
)
_DBS_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "dbs"
)
# Eval workspaces live on real disk, not /tmp (tmpfs quotas break large
# npm installs). ~/.cache keeps XDG compliance and survives reboots.
_DEFAULT_WORKSPACE_ROOT = Path.home() / ".cache" / "omni-code" / "eval-workspaces"

_LAUNCHER_DEFAULT_MCP_CLI = (
    Path.home()
    / "Omni"
    / "Workspace"
    / "launcher"
    / "packages"
    / "projects-mcp"
    / "dist"
    / "cli.js"
)


def _resolve_mcp_cli() -> Path | None:
    """Locate the omni-projects-mcp cli.js.

    Resolution order:
      1. ``$OMNI_PROJECTS_MCP_CLI`` (absolute path)
      2. Launcher default build at
         ``~/Omni/Workspace/launcher/packages/projects-mcp/dist/cli.js``
    """
    env_path = os.environ.get("OMNI_PROJECTS_MCP_CLI")
    if env_path:
        p = Path(env_path).expanduser()
        return p if p.is_file() else None
    if _LAUNCHER_DEFAULT_MCP_CLI.is_file():
        return _LAUNCHER_DEFAULT_MCP_CLI
    return None


class WorkspaceEvalEnvironment(LocalWorkspaceEnvironment):
    """omni-code's temp-workspace eval environment.

    MCP-backed project state (``db_fixture`` field on a scenario):
        Stages an isolated ``XDG_CONFIG_HOME`` inside the temp dir (via
        the framework's ``UserMcpFixtureStager``) with an
        ``omni_code/mcp.json`` pointing at the real ``omni-projects-mcp``
        cli (resolved via ``$OMNI_PROJECTS_MCP_CLI`` or the launcher's
        default build path). The agent picks it up via the standard
        ``allow_user_mcp_servers`` path; the spawned cli opens the
        per-scenario ``projects.db`` instead of the user's real one.

        If ``evaluations/fixtures/dbs/<name>.db`` exists it is copied;
        otherwise the cli initializes a fresh schema on first open.

        The ``XDG_CONFIG_HOME`` override flows through ``get_env_vars()``
        — the eval runner applies environment env vars before spec load
        (where user-MCP config is discovered), so no os.environ mutation
        or cross-scenario lock is needed and ``db_fixture`` scenarios
        stay parallelisable.
    """

    def __init__(self, fixtures_dir: str | None = None, **kwargs: Any) -> None:
        super().__init__(
            fixtures_dir=fixtures_dir or _DEFAULT_FIXTURES_DIR,
            workspace_cache_dir=_DEFAULT_WORKSPACE_ROOT,
            skills_subdir=".omni_code/skills",
            keep_tempdir_env="OMNI_EVAL_KEEP_TEMPDIR",
            tempdir_prefix="omni_eval_",
            **kwargs,
        )

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        context = await super().setup(scenario)
        tmp_dir = context["tmp_dir"]

        # Stage MCP-backed project DB if requested. Done last so a failure
        # here surfaces with the workspace already set up (easier to debug).
        db_fixture = scenario.get("db_fixture")
        db_path: Path | None = None
        mcp_config_dir: Path | None = None
        mcp_env: Dict[str, str] | None = None
        if db_fixture:
            mcp_cli = _resolve_mcp_cli()
            if mcp_cli is None:
                env_set = os.environ.get("OMNI_PROJECTS_MCP_CLI")
                if env_set:
                    raise RuntimeError(
                        f"OMNI_PROJECTS_MCP_CLI={env_set!r} does not point to a file"
                    )
                raise RuntimeError(
                    "Scenario specifies db_fixture but omni-projects-mcp cli is "
                    "not available. Set OMNI_PROJECTS_MCP_CLI to a path to "
                    "packages/projects-mcp/dist/cli.js, or build the launcher "
                    f"so the default path resolves: {_LAUNCHER_DEFAULT_MCP_CLI}"
                )

            stager = UserMcpFixtureStager(tmp_dir, product_config_name="omni_code")
            db_path = stager.stage_file(
                _DBS_FIXTURES_DIR / f"{db_fixture}.db", "projects.db"
            )
            pages_dir = stager.stage_tree(
                _DBS_FIXTURES_DIR / f"{db_fixture}.pages", "projects"
            )

            node_bin = os.environ.get("OMNI_PROJECTS_MCP_NODE", "node")
            stager.write_mcp_json(
                {
                    "omni-projects": {
                        "type": "stdio",
                        "command": node_bin,
                        "args": [
                            str(mcp_cli),
                            "--db-path",
                            str(db_path),
                            "--pages-dir",
                            str(pages_dir),
                        ],
                    }
                }
            )
            mcp_config_dir = stager.config_dir
            mcp_env = stager.env_vars()

            print(
                f"[WorkspaceEvalEnvironment] db_fixture={db_fixture!r}: "
                f"XDG_CONFIG_HOME={stager.xdg_root} db_path={db_path} "
                f"mcp_cli={mcp_cli}"
            )

        context.data.update(
            {
                "db_fixture": db_fixture,
                "db_path": str(db_path) if db_path else None,
                "mcp_config_dir": str(mcp_config_dir) if mcp_config_dir else None,
                "_mcp_env": mcp_env,
            }
        )
        return context

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        env = super().get_env_vars(context)
        tmp_dir = context.get("workspace_root", "")
        env.update(
            {
                "OMNI_WORKSPACE_ROOT": tmp_dir,
                # Disable real web API calls during evaluation
                "SERPAPI_API_KEY": "",
                # Auto-trust mise.toml inside any eval workspace. Without
                # this, fixtures that ship a mise.toml (e.g. Rails projects
                # pinning a Ruby version) cause every shell invocation to
                # die with "Config files in <path> are not trusted" before
                # the command even runs. Scoped to the eval workspace root,
                # so only paths we ourselves stage are trusted.
                "MISE_TRUSTED_CONFIG_PATHS": str(_DEFAULT_WORKSPACE_ROOT),
            }
        )
        # The staged XDG_CONFIG_HOME (db_fixture scenarios) — the runner
        # applies these before spec load, where user-MCP config is read.
        mcp_env = context.get("_mcp_env")
        if isinstance(mcp_env, dict):
            env.update(mcp_env)
        return env
