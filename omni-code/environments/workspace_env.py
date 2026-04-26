"""Lightweight evaluation environment using temporary workspaces.

Creates a temp directory per scenario, copies fixture files into it,
and sets environment variables so the omni-code agent treats it as
its workspace. No Docker required.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict

from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment

_DEFAULT_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "workspaces"
)
_DBS_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "dbs"
)
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


class WorkspaceEvalEnvironment(EvalEnvironment):
    """Evaluation environment backed by disposable temp directories.

    Each scenario gets its own temp directory. If the scenario specifies a
    ``fixture`` key, that subdirectory is copied from the fixtures tree into
    the temp dir. The agent sees the temp dir as its workspace via the
    ``PWD`` environment variable.

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
        self.fixtures_dir = (
            Path(fixtures_dir) if fixtures_dir else _DEFAULT_FIXTURES_DIR
        )

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        session_id = str(uuid.uuid4())
        tmp_dir = tempfile.mkdtemp(prefix="omni_eval_")

        # Copy fixture workspace if specified
        fixture_name = scenario.get("fixture")
        if fixture_name:
            fixture_src = self.fixtures_dir / fixture_name
            if fixture_src.is_dir():
                shutil.copytree(fixture_src, tmp_dir, dirs_exist_ok=True)

        # Write inline AGENTS.md if provided in scenario
        agents_md_content = scenario.get("agents_md_content")
        if agents_md_content:
            (Path(tmp_dir) / "AGENTS.md").write_text(agents_md_content)

        # Write inline skill definitions if provided in scenario
        skills = scenario.get("skills")
        if skills and isinstance(skills, dict):
            for skill_name, skill_content in skills.items():
                skill_dir = Path(tmp_dir) / ".omni_code" / "skills" / skill_name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(skill_content)

        # Initialize a git repo so git-dependent operations don't fail
        _init_git_repo(tmp_dir)

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

        # Path to the pristine (pre-agent) fixture directory. Measures use
        # this to compare or to revert specific files for regression checks.
        fixture_src_dir = (
            str(self.fixtures_dir / fixture_name) if fixture_name else None
        )

        return EnvironmentContext(
            session_id=session_id,
            data={
                "workspace_root": tmp_dir,
                "fixture": fixture_name,
                "fixture_src_dir": fixture_src_dir,
                "tmp_dir": tmp_dir,
                "db_fixture": db_fixture,
                "db_path": str(db_path) if db_path else None,
                "mcp_config_dir": str(mcp_config_dir) if mcp_config_dir else None,
                "_original_xdg_config_home": original_xdg,
                "_xdg_overridden": xdg_overridden,
                "_xdg_lock_held": xdg_lock_held,
            },
        )

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

        tmp_dir = context.get("tmp_dir")
        if tmp_dir and Path(tmp_dir).exists():
            if os.environ.get("OMNI_EVAL_KEEP_TEMPDIR"):
                # Diagnostic mode: leave the workspace + staged xdg dir on
                # disk so the run can be inspected post-hoc.
                print(
                    f"[WorkspaceEvalEnvironment] OMNI_EVAL_KEEP_TEMPDIR set — "
                    f"preserving {tmp_dir}"
                )
                return
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        tmp_dir = context.get("workspace_root", "")
        return {
            "PWD": tmp_dir,
            "OMNI_WORKSPACE_ROOT": tmp_dir,
            # Disable real web API calls during evaluation
            "SERPAPI_API_KEY": "",
        }

    async def verify(
        self, context: EnvironmentContext, expected: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify filesystem state in the temp workspace.

        ``expected`` maps relative paths to expectation dicts::

            {
                "src/main.py": {
                    "exists": True,
                    "contains": ["def hello"],
                    "not_contains": ["import os"],
                },
                "secrets.txt": {"exists": False},
            }
        """
        tmp_dir = Path(context.get("workspace_root", ""))
        results: Dict[str, Any] = {}
        success = True

        for rel_path, expectations in expected.items():
            full_path = tmp_dir / rel_path
            expect_exists = expectations.get("exists", True)

            if expect_exists:
                if not full_path.exists():
                    results[rel_path] = {"matched": False, "reason": "file not found"}
                    success = False
                    continue

                content = full_path.read_text(errors="replace")

                # Check required content
                for pattern in expectations.get("contains", []):
                    if pattern not in content:
                        results[rel_path] = {
                            "matched": False,
                            "reason": f"missing pattern: {pattern!r}",
                        }
                        success = False
                        break
                else:
                    # Check forbidden content
                    for pattern in expectations.get("not_contains", []):
                        if pattern in content:
                            results[rel_path] = {
                                "matched": False,
                                "reason": f"forbidden pattern found: {pattern!r}",
                            }
                            success = False
                            break
                    else:
                        results[rel_path] = {"matched": True}
            else:
                if full_path.exists():
                    results[rel_path] = {
                        "matched": False,
                        "reason": "file should not exist",
                    }
                    success = False
                else:
                    results[rel_path] = {"matched": True}

        return {"success": success, "results": results}


def _init_git_repo(path: str) -> None:
    """Initialize a minimal git repo with an initial commit."""
    try:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "eval@test.local"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Eval"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "initial fixture", "--allow-empty"],
            cwd=path,
            check=True,
            capture_output=True,
        )
    except Exception:
        pass
