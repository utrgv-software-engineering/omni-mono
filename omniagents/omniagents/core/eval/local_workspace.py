"""Local temp-directory evaluation environment.

Creates a disposable workspace per scenario: a temp directory under a
real-disk cache root, a fixture copy that respects .gitignore, optional
inline AGENTS.md / skill files, and a git repo so git-dependent agent
operations work. Teardown reaps any processes the agent left running in
the workspace and removes the directory (unless the keep-tempdir env var
is set for post-hoc inspection).

Lifted from omni-code. Products subclass to add their own staging (e.g.
MCP-backed database fixtures) and extra environment variables.
"""

from __future__ import annotations

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
from omniagents.core.utils.fs import remove_tree

__all__ = ["LocalWorkspaceEnvironment"]


class LocalWorkspaceEnvironment(EvalEnvironment):
    """Evaluation environment backed by disposable temp directories.

    Each scenario gets its own temp directory. If the scenario specifies
    a ``fixture`` key, that subdirectory is copied from *fixtures_dir*
    into the temp dir (gitignore-respecting). ``agents_md_content``
    writes an inline AGENTS.md; ``skills`` (name → SKILL.md content)
    stages inline skills under *skills_subdir*.

    Args:
        fixtures_dir: Root of the fixture workspaces tree.
        workspace_cache_dir: Where eval workspaces live. Defaults to
            ``~/.cache/omniagents/eval-workspaces`` — real disk, not
            /tmp: tmpfs quotas break large installs (e.g. node_modules)
            with opaque EDQUOT errors, and surviving reboots aids
            inspection.
        skills_subdir: Workspace-relative directory for inline skills
            (e.g. ``.omni_code/skills``). Defaults to
            ``.omniagents/skills``.
        keep_tempdir_env: Env var that, when set, preserves workspaces
            at teardown for post-mortem inspection.
        tempdir_prefix: Prefix for the per-scenario temp directories.
    """

    def __init__(
        self,
        fixtures_dir: str | Path | None = None,
        *,
        workspace_cache_dir: str | Path | None = None,
        skills_subdir: str = ".omniagents/skills",
        keep_tempdir_env: str = "OMNIAGENTS_EVAL_KEEP_TEMPDIR",
        tempdir_prefix: str = "omniagents_eval_",
        **kwargs: Any,
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self.workspace_cache_dir = (
            Path(workspace_cache_dir)
            if workspace_cache_dir
            else Path.home() / ".cache" / "omniagents" / "eval-workspaces"
        )
        self.skills_subdir = skills_subdir
        self.keep_tempdir_env = keep_tempdir_env
        self.tempdir_prefix = tempdir_prefix

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        session_id = str(uuid.uuid4())
        self.workspace_cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(
            prefix=self.tempdir_prefix, dir=self.workspace_cache_dir
        )

        # Copy fixture workspace if specified.
        #
        # Respect .gitignore so build artifacts (.venv, node_modules,
        # __pycache__, sqlite DBs, etc.) accumulated by anyone working
        # inside the fixture don't ship into every eval tempdir: a stale
        # .venv against a missing interpreter silently breaks `uv run`,
        # and a 250 MB node_modules slows every scenario down.
        fixture_name = scenario.get("fixture")
        if fixture_name and self.fixtures_dir is not None:
            fixture_src = self.fixtures_dir / fixture_name
            if fixture_src.is_dir():
                copy_fixture_respecting_gitignore(fixture_src, Path(tmp_dir))

        # Write inline AGENTS.md if provided in scenario
        agents_md_content = scenario.get("agents_md_content")
        if agents_md_content:
            (Path(tmp_dir) / "AGENTS.md").write_text(agents_md_content)

        # Write inline skill definitions if provided in scenario
        skills = scenario.get("skills")
        if skills and isinstance(skills, dict):
            for skill_name, skill_content in skills.items():
                skill_dir = Path(tmp_dir) / self.skills_subdir / skill_name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(skill_content)

        # Initialize a git repo so git-dependent operations don't fail
        init_git_repo(tmp_dir)

        # Path to the pristine (pre-agent) fixture directory. Measures use
        # this to compare or to revert specific files for regression checks.
        fixture_src_dir = (
            str(self.fixtures_dir / fixture_name)
            if fixture_name and self.fixtures_dir is not None
            else None
        )

        return EnvironmentContext(
            session_id=session_id,
            data={
                "workspace_root": tmp_dir,
                "fixture": fixture_name,
                "fixture_src_dir": fixture_src_dir,
                "tmp_dir": tmp_dir,
            },
        )

    async def teardown(self, context: EnvironmentContext) -> None:
        tmp_dir = context.get("tmp_dir")
        if tmp_dir and Path(tmp_dir).exists():
            # Kill any long-lived child the agent spawned inside the
            # workspace (vite, uvicorn --reload, etc.) before deleting
            # the dir, otherwise they leak across runs and hold ports.
            try:
                killed = kill_workspace_processes(tmp_dir)
                if killed:
                    print(
                        f"[{type(self).__name__}] killed {len(killed)} "
                        f"workspace process(es): {killed}"
                    )
            except Exception as exc:
                print(f"[{type(self).__name__}] process cleanup error: {exc}")

            if os.environ.get(self.keep_tempdir_env):
                # Diagnostic mode: leave the workspace on disk so the run
                # can be inspected post-hoc.
                print(
                    f"[{type(self).__name__}] {self.keep_tempdir_env} set — "
                    f"preserving {tmp_dir}"
                )
                return
            remove_tree(tmp_dir, ignore_errors=True)

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        tmp_dir = context.get("workspace_root", "")
        return {"PWD": tmp_dir}


def kill_workspace_processes(
    workspace_root: str, *, term_wait_s: float = 2.0
) -> List[int]:
    """Kill any process whose CWD lives at or under ``workspace_root``.

    Agents typically spawn long-lived dev servers (``npm run dev``,
    ``vite``, ``uvicorn --reload``) inside the workspace. The eval
    framework deletes the workspace at teardown but does NOT track those
    children, so they leak across runs and hold ports that the next
    run's agent then collides with.

    Walks ``/proc/<pid>/cwd`` symlinks (Linux only), SIGTERMs survivors
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


def copy_fixture_respecting_gitignore(src: Path, dst: Path) -> None:
    """Copy fixture contents excluding gitignored paths.

    Uses ``git ls-files -coz --exclude-standard`` from inside ``src`` to
    enumerate tracked files plus untracked-but-not-ignored files (the
    same set ``git status`` shows). Falls back to ``shutil.copytree`` if
    ``src`` isn't a git working tree, so this is safe to use on
    fixtures that aren't versioned.
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
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
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


def init_git_repo(path: str) -> None:
    """Initialize a minimal git repo with an initial commit. Best-effort."""
    try:
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
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
        subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "initial fixture", "--allow-empty"],
            cwd=path,
            check=True,
            capture_output=True,
        )
    except Exception:
        pass
