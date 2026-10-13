"""Host commands on Windows run through Git Bash, never cmd.exe.

These simulate Windows on any CI OS; the Windows CI job exercises the real
thing end to end.
"""

import ntpath
import posixpath

import pytest

from omniagents.builtin.tools import _bash_jobs
from omniagents.workspace import host as host_module
from omniagents.workspace import host_shell
from omniagents.workspace.host import HostWorkspace
from omniagents.workspace.host_shell import HostShellUnavailable


def _fake_git(tmp_path):
    root = tmp_path / "Git"
    (root / "bin").mkdir(parents=True)
    (root / "cmd").mkdir()
    (root / "usr" / "bin").mkdir(parents=True)
    bash = root / "bin" / "bash.exe"
    bash.write_text("")
    git = root / "cmd" / "git.exe"
    git.write_text("")
    return root, bash, git


def test_posix_hosts_keep_the_system_shell(monkeypatch):
    monkeypatch.setattr(host_shell.sys, "platform", "linux")
    assert host_shell.host_shell_command("echo hi", None) is None


def test_windows_runs_commands_through_explicit_bash(monkeypatch, tmp_path):
    root, bash, _ = _fake_git(tmp_path)
    monkeypatch.setattr(host_shell.sys, "platform", "win32")
    monkeypatch.setenv(host_shell.BASH_ENV_VAR, str(bash))
    argv, env = host_shell.host_shell_command("export X=1 && echo $X", {"PATH": "P"})
    assert argv == [str(bash), "-c", "export X=1 && echo $X"]
    # Git's Unix tools win over same-named Windows programs (find, sort).
    assert env["PATH"].split(host_shell.os.pathsep)[0] == str(root / "usr" / "bin")
    assert env["PATH"].endswith("P")
    assert env["CHERE_INVOKING"] == "1"


def test_windows_finds_installed_git_bash_from_git_on_path(monkeypatch, tmp_path):
    _, bash, git = _fake_git(tmp_path)
    monkeypatch.setattr(host_shell.sys, "platform", "win32")
    monkeypatch.delenv(host_shell.BASH_ENV_VAR, raising=False)
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host_shell.shutil, "which", lambda name: str(git))
    assert host_shell.find_windows_bash() == str(bash)


def test_windows_without_bash_refuses_instead_of_using_cmd(monkeypatch):
    monkeypatch.setattr(host_shell.sys, "platform", "win32")
    monkeypatch.delenv(host_shell.BASH_ENV_VAR, raising=False)
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host_shell.shutil, "which", lambda name: None)
    with pytest.raises(HostShellUnavailable, match="Git Bash"):
        host_shell.host_shell_command("ls", None)


def test_wsl_launcher_is_never_selected(monkeypatch, tmp_path):
    system32 = tmp_path / "Windows" / "System32"
    system32.mkdir(parents=True)
    (system32 / "bash.exe").write_text("")
    monkeypatch.setattr(host_shell.sys, "platform", "win32")
    monkeypatch.delenv(host_shell.BASH_ENV_VAR, raising=False)
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host_shell.shutil, "which", lambda name: None)
    assert host_shell.find_windows_bash() is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/c/Users/student/app.py", "C:\\Users\\student\\app.py"),
        ("/d", "D:\\"),
        ("/workspace/app.py", "/workspace/app.py"),
        ("/cd/x", "/cd/x"),
        ("src/app.py", "src/app.py"),
    ],
)
def test_git_bash_drive_paths_translate_on_windows(monkeypatch, given, expected):
    monkeypatch.setattr(host_module.os, "name", "nt")
    assert host_module._from_git_bash_path(given) == expected


def test_git_bash_paths_untouched_elsewhere(monkeypatch):
    monkeypatch.setattr(host_module.os, "name", "posix")
    assert host_module._from_git_bash_path("/c/Users/x") == "/c/Users/x"


def test_workspace_path_syntax():
    # Sandbox/container/wasm paths are POSIX on any host OS; the host
    # workspace uses the host's own path rules.
    from omniagents.workspace.base import Workspace

    assert Workspace.path_module is posixpath
    assert HostWorkspace.path_module is host_module.os.path


def test_job_logs_follow_workspace_path_syntax():
    assert (
        _bash_jobs.log_path_for("/workspace", "bash-1")
        == "/workspace/.omniagents/jobs/bash-1.log"
    )
    assert (
        _bash_jobs.log_path_for("C:\\proj", "bash-1", ntpath)
        == "C:\\proj\\.omniagents\\jobs\\bash-1.log"
    )


@pytest.mark.parametrize(
    "shell",
    ["bash -i", "/bin/bash", "bash", "", '"C:\\Program Files\\Git\\bin\\bash.exe"'],
)
def test_windows_terminal_shell_resolves_to_git_bash(monkeypatch, tmp_path, shell):
    from omniagents.rpc import terminal_backends

    root, bash, _ = _fake_git(tmp_path)
    monkeypatch.setattr(host_shell.sys, "platform", "win32")
    monkeypatch.setenv(host_shell.BASH_ENV_VAR, str(bash))
    argv, env = terminal_backends._windows_shell_argv(shell)
    assert argv[0] == str(bash)
    assert "-i" in argv
    assert env["CHERE_INVOKING"] == "1"
    assert env["TERM"] == "xterm-256color"


def test_windows_terminal_keeps_non_posix_shells(monkeypatch):
    from omniagents.rpc import terminal_backends

    argv, _ = terminal_backends._windows_shell_argv("pwsh -NoLogo")
    assert argv == ["pwsh", "-NoLogo"]
