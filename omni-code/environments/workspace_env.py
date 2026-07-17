"""Lightweight evaluation environment using temporary workspaces.

A product subclass of the framework's ``LocalWorkspaceEnvironment``: the
tempdir staging, gitignore-respecting fixture copy, inline AGENTS.md /
skills, git init, process reaping, and keep-tempdir diagnostics all live
framework-side. This class adds omni-code's parts — the fixture-tree
default, product env vars, and MCP-backed project state (``db_fixture``).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from omniagents.core.eval.environment import EnvironmentContext
from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment

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
        Stages an isolated ``XDG_CONFIG_HOME`` inside the temp dir with an
        ``omni_code/mcp.json`` pointing at the real ``omni-projects-mcp``
        cli (resolved via ``$OMNI_PROJECTS_MCP_CLI`` or the launcher's
        default build path). The agent picks it up via the standard
        ``allow_user_mcp_servers`` path; the spawned cli opens the
        per-scenario ``projects.db`` instead of the user's real one.

        If ``evaluations/fixtures/dbs/<name>.db`` exists it is copied;
        otherwise the cli initializes a fresh schema on first open.

        Self-serialising: ``db_fixture`` scenarios mutate
        ``os.environ['XDG_CONFIG_HOME']`` (because spec loading reads it
        before ``get_env_vars()`` applies). To stay correct under
        ``runs_per_prompt`` and parallel scenarios, ``setup()`` holds a
        class-level lock that ``teardown()`` releases. Scenarios that
        don't use ``db_fixture`` are unaffected and stay parallelisable.
    """

    # Class-level lock serialising any setup/teardown pair that mutates
    # XDG_CONFIG_HOME. Lazy-init because asyncio.Lock binds to the running
    # loop on first await.
    _xdg_lock: "asyncio.Lock | None" = None

    @classmethod
    def _get_xdg_lock(cls) -> asyncio.Lock:
        if cls._xdg_lock is None:
            cls._xdg_lock = asyncio.Lock()
        return cls._xdg_lock

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
        original_xdg: str | None = None
        xdg_overridden = False
        db_path: Path | None = None
        mcp_config_dir: Path | None = None
        xdg_lock_held = False
        if db_fixture:
            # Acquire the class-level XDG lock NOW; teardown releases it.
            # Serialises overlapping db_fixture scenarios so they don't
            # stomp on each other's os.environ['XDG_CONFIG_HOME'].
            await self._get_xdg_lock().acquire()
            xdg_lock_held = True
            try:
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

                xdg_root = Path(tmp_dir) / "_xdg_config"
                mcp_config_dir = xdg_root / "omni_code"
                mcp_config_dir.mkdir(parents=True, exist_ok=True)

                db_path = mcp_config_dir / "projects.db"
                pages_dir = mcp_config_dir / "projects"
                pages_dir.mkdir(parents=True, exist_ok=True)

                fixture_db = _DBS_FIXTURES_DIR / f"{db_fixture}.db"
                if fixture_db.is_file():
                    shutil.copy2(fixture_db, db_path)
                fixture_pages = _DBS_FIXTURES_DIR / f"{db_fixture}.pages"
                if fixture_pages.is_dir():
                    shutil.copytree(fixture_pages, pages_dir, dirs_exist_ok=True)

                node_bin = os.environ.get("OMNI_PROJECTS_MCP_NODE", "node")
                mcp_json = {
                    "mcpServers": {
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
                }
                (mcp_config_dir / "mcp.json").write_text(
                    json.dumps(mcp_json, indent=2) + "\n"
                )

                # Mutate XDG_CONFIG_HOME now: spec loading
                # (omniagents/core/project/runtime.py:283) reads it before the
                # runner applies env vars from get_env_vars().
                original_xdg = os.environ.get("XDG_CONFIG_HOME")
                os.environ["XDG_CONFIG_HOME"] = str(xdg_root)
                xdg_overridden = True

                print(
                    f"[WorkspaceEvalEnvironment] db_fixture={db_fixture!r}: "
                    f"XDG_CONFIG_HOME={xdg_root} db_path={db_path} mcp_cli={mcp_cli}"
                )
            except Exception:
                # Anything in the staging block failed — release the lock so
                # we don't deadlock the next scenario (teardown won't run for
                # a failed setup).
                self._get_xdg_lock().release()
                xdg_lock_held = False
                raise

        context.data.update(
            {
                "db_fixture": db_fixture,
                "db_path": str(db_path) if db_path else None,
                "mcp_config_dir": str(mcp_config_dir) if mcp_config_dir else None,
                "_original_xdg_config_home": original_xdg,
                "_xdg_overridden": xdg_overridden,
                "_xdg_lock_held": xdg_lock_held,
            }
        )
        return context

    async def teardown(self, context: EnvironmentContext) -> None:
        # Restore XDG_CONFIG_HOME first — runs even if the agent crashed,
        # so a failure mid-cleanup doesn't leak the override into the
        # next scenario.
        try:
            if context.get("_xdg_overridden"):
                original = context.get("_original_xdg_config_home")
                if original is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original
        finally:
            # Release the XDG lock unconditionally if we acquired it.
            # Held across the entire setup → agent run → measures →
            # teardown lifecycle so other scenarios can't read os.environ
            # mid-flight.
            if context.get("_xdg_lock_held"):
                try:
                    self._get_xdg_lock().release()
                except RuntimeError:
                    # Already released (e.g. by a failed setup). Safe to ignore.
                    pass

        await super().teardown(context)

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
        return env
