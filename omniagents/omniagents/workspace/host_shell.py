"""The POSIX shell that runs host-side ``shell=True`` commands.

Agent tools speak bash: ``execute_bash`` commands, background-job wrappers
(``( cmd ) >> log 2>&1 < /dev/null``), and framework plumbing such as
``mkdir -p`` / ``test -e``. ``create_subprocess_shell`` would use the
system shell instead: ``/bin/sh`` on POSIX, which is dash on Debian/Ubuntu
(and therefore in WSL), so bash syntax (``[[ ]]``, arrays, ``source``)
failed there; ``cmd.exe`` on Windows, where none of it works. Host commands
therefore run through bash: the one on PATH on POSIX hosts (``/bin/sh``
only when no bash is installed), Git for Windows' bash on Windows.

Resolution on Windows, first match wins:

1. ``OMNIAGENTS_BASH`` — an explicit ``bash.exe`` (the desktop launcher
   points this at the Git for Windows copy it provisions).
2. An installed Git for Windows, found from ``git`` on ``PATH`` or its
   standard install locations.

``C:\\Windows\\System32\\bash.exe`` is never used: that is the WSL launcher,
whose commands run in a different filesystem with different paths.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .types import WorkspaceFeatureUnavailable

__all__ = [
    "BASH_ENV_VAR",
    "HostShellUnavailable",
    "find_windows_bash",
    "host_shell_command",
    "host_shell_env",
]

BASH_ENV_VAR = "OMNIAGENTS_BASH"

_MISSING_BASH_MESSAGE = (
    "Shell commands on Windows need Git Bash, and none was found. "
    "Install Git for Windows (https://gitforwindows.org) or set "
    f"{BASH_ENV_VAR} to a bash.exe; the Omni desktop app provisions one "
    "automatically during runtime setup."
)


class HostShellUnavailable(WorkspaceFeatureUnavailable):
    """No usable POSIX shell for host commands on this machine."""


def _git_root_for(bash: Path) -> Optional[Path]:
    """Git for Windows root for a ``<root>\\bin\\bash.exe`` or
    ``<root>\\usr\\bin\\bash.exe``."""
    parent = bash.parent
    if parent.name.lower() == "bin" and parent.parent.name.lower() == "usr":
        return parent.parent.parent
    if parent.name.lower() == "bin":
        return parent.parent
    return None


def _candidate_roots() -> List[Path]:
    roots: List[Path] = []
    git = shutil.which("git")
    if git:
        # <root>\cmd\git.exe, <root>\bin\git.exe, or <root>\mingw64\bin\git.exe
        exe = Path(git).resolve()
        for ancestor in exe.parents:
            if (ancestor / "bin" / "bash.exe").is_file():
                roots.append(ancestor)
                break
    for env_name, suffix in (
        ("ProgramFiles", "Git"),
        ("ProgramW6432", "Git"),
        ("ProgramFiles(x86)", "Git"),
        ("LOCALAPPDATA", os.path.join("Programs", "Git")),
    ):
        base = os.environ.get(env_name)
        if base:
            roots.append(Path(base) / suffix)
    return roots


def find_windows_bash() -> Optional[str]:
    """Absolute path of a Git for Windows ``bash.exe``, or None."""
    explicit = os.environ.get(BASH_ENV_VAR, "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    for root in _candidate_roots():
        candidate = root / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def host_shell_env(env: Optional[Dict[str, str]], bash: str) -> Dict[str, str]:
    """Environment for a Git Bash child: Git's Unix tools first on PATH.

    ``bin\\bash.exe`` is a redirector that also prepends these, but the
    explicit prefix keeps ``usr\\bin`` tools (``ls``, ``grep``, ``sed``)
    ahead of same-named Windows programs (``find``, ``sort``) for any child
    started without that redirector. ``CHERE_INVOKING`` keeps a login
    profile from changing directory away from the requested cwd.
    """
    merged = dict(os.environ if env is None else env)
    root = _git_root_for(Path(bash))
    if root is not None:
        prefix = [
            str(root / "usr" / "bin"),
            str(root / "mingw64" / "bin"),
            str(root / "bin"),
        ]
        path_key = next((k for k in merged if k.upper() == "PATH"), "PATH")
        existing = merged.get(path_key, "")
        merged[path_key] = os.pathsep.join(prefix + ([existing] if existing else []))
    merged.setdefault("CHERE_INVOKING", "1")
    return merged


def host_shell_command(
    command: str, env: Optional[Dict[str, str]]
) -> Optional[tuple[List[str], Optional[Dict[str, str]]]]:
    """Argv + env that run *command* through bash.

    POSIX: ``bash -c`` when bash is installed, else None (the caller uses
    the system shell). Windows: Git Bash; raises
    :class:`HostShellUnavailable` when none is available rather than
    falling back to ``cmd.exe`` and running bash syntax in the wrong shell.
    """
    if sys.platform != "win32":
        bash = shutil.which("bash")
        return ([bash, "-c", command], env) if bash else None
    bash = find_windows_bash()
    if bash is None:
        raise HostShellUnavailable(_MISSING_BASH_MESSAGE)
    return [bash, "-c", command], host_shell_env(env, bash)
