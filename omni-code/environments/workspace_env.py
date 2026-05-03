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
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment

_DEFAULT_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "workspaces"
)
_DBS_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "dbs"
)
# Eval workspaces live on real disk, not /tmp. /tmp is tmpfs on most
# Linux setups and enforces a per-user quota; `npm install` for a Vite
# frontend (~250 MB) will hit EDQUOT mid-extract and fail with the
# opaque "Unknown system error -122". Default to ~/.cache to stay XDG-
# compliant and survive across reboots for inspection.
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
        _DEFAULT_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix="omni_eval_", dir=_DEFAULT_WORKSPACE_ROOT)

        # Copy fixture workspace if specified.
        #
        # Respect .gitignore so build artifacts (.venv, node_modules,
        # __pycache__, sqlite DBs, etc.) accumulated by anyone working
        # inside the fixture don't ship into every eval tempdir.
        # `shutil.copytree` is a footgun here: a stale .venv created
        # against an interpreter that no longer exists silently breaks
        # `uv run` in every run, and a 250 MB node_modules slows every
        # scenario down. Treat the fixture as "tracked files + anything
        # in the working tree that isn't ignored" — same semantics as
        # `git archive HEAD` plus untracked-but-intentional files.
        fixture_name = scenario.get("fixture")
        if fixture_name:
            fixture_src = self.fixtures_dir / fixture_name
            if fixture_src.is_dir():
                _copy_fixture_respecting_gitignore(fixture_src, Path(tmp_dir))

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
            # Kill any long-lived child the agent spawned inside the
            # workspace (vite, uvicorn --reload, etc.) before deleting
            # the dir, otherwise they leak across runs and hold ports.
            try:
                killed = _kill_workspace_processes(tmp_dir)
                if killed:
                    print(
                        f"[WorkspaceEvalEnvironment] killed {len(killed)} "
                        f"workspace process(es): {killed}"
                    )
            except Exception as exc:
                print(f"[WorkspaceEvalEnvironment] process cleanup error: {exc}")

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
            # Auto-trust mise.toml inside any eval workspace. Without this,
            # fixtures that ship a mise.toml (e.g. Rails projects pinning a
            # Ruby version) cause every shell invocation to die with
            # "Config files in <path> are not trusted" before the command
            # even runs. Scoped to the eval workspace root, so only paths
            # we ourselves stage are trusted.
            "MISE_TRUSTED_CONFIG_PATHS": str(_DEFAULT_WORKSPACE_ROOT),
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


def _kill_workspace_processes(workspace_root: str, *, term_wait_s: float = 2.0) -> List[int]:
    """Kill any process whose CWD lives at or under ``workspace_root``.

    The agent typically spawns long-lived dev servers (``npm run dev``,
    ``vite``, ``uvicorn --reload``) inside the workspace. The eval
    framework deletes the workspace at teardown but does NOT track or
    terminate those children, so they leak across runs and hold ports
    that the next run's agent then collides with — most visibly, a
    stale ``vite`` on ``:5173`` returning 404 to the next acceptance
    runner's ``_wait_for_url`` probe and tanking the manifest measure.

    Walks ``/proc/<pid>/cwd`` symlinks (Linux only — fine here, the
    eval cache directory is Linux-style anyway), SIGTERMs survivors
    rooted under the workspace, waits briefly, then SIGKILLs anything
    still alive. Returns the list of PIDs that were targeted.
    """
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []  # not Linux
    workspace_real = os.path.realpath(workspace_root)
    self_pid = os.getpid()
    targets: List[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == self_pid:
            continue
        try:
            cwd = os.readlink(entry / "cwd")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        cwd_real = os.path.realpath(cwd)
        if cwd_real == workspace_real or cwd_real.startswith(workspace_real + os.sep):
            targets.append(pid)

    if not targets:
        return []

    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.monotonic() + term_wait_s
    survivors = list(targets)
    while survivors and time.monotonic() < deadline:
        survivors = [pid for pid in survivors if (proc_root / str(pid)).exists()]
        if survivors:
            time.sleep(0.1)

    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    return targets


def _copy_fixture_respecting_gitignore(src: Path, dst: Path) -> None:
    """Copy fixture contents excluding gitignored paths.

    Uses ``git ls-files -coz --exclude-standard`` from inside ``src`` to
    enumerate tracked files plus untracked-but-not-ignored files (the
    same set ``git status`` shows). Falls back to ``shutil.copytree`` if
    ``src`` isn't a git working tree, so this is safe to use on
    fixtures that aren't versioned.

    The omni-code repo itself contains the fixture, so the git query
    runs against the parent repo and reports paths relative to ``src``.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(src),
                "ls-files",
                "-coz",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Not a git checkout (or git missing); fall back to verbatim copy.
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return

    rel_paths = [p for p in result.stdout.decode().split("\0") if p]
    dst.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        src_path = src / rel
        if not src_path.is_file() and not src_path.is_symlink():
            # `ls-files -o` can list directories for unmerged paths; skip
            # anything that isn't a regular file.
            continue
        dst_path = dst / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path, follow_symlinks=False)


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
