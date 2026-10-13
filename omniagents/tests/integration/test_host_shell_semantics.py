"""Agent shell commands get real bash semantics on the host, on every OS.

On Windows this runs through Git Bash (the runner has Git for Windows); the
same assertions hold on POSIX hosts. Regression guard for host commands
silently running under cmd.exe.
"""

import asyncio
import json
import os
import sys
import time

import pytest
from agents.tool import ToolContext
from agents.usage import Usage

import omniagents.builtin.tools.bash_tool as bash_tool
import omniagents.builtin.tools.file_tools as file_tools
from omniagents.builtin.tools import _bash_jobs
from omniagents.workspace import reset_workspace, set_workspace
from omniagents.workspace.host import HostWorkspace


@pytest.fixture
def host_workspace(tmp_path):
    root = tmp_path / "student project"
    root.mkdir()
    workspace = HostWorkspace(root=str(root))
    token = set_workspace(workspace)
    try:
        yield workspace
    finally:
        reset_workspace(token)


async def _invoke(tool, root, **params):
    payload = {k: v for k, v in params.items() if v is not None}
    ctx = ToolContext(
        context={"workspace_root": root},
        usage=Usage(),
        tool_name=tool.name,
        tool_call_id="call",
        tool_arguments=payload,
    )
    return await tool.on_invoke_tool(ctx, json.dumps(payload))


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("export X=42 && echo $X", "42"),
        ("echo 'a b'", "a b"),
        ('echo "$((6 * 7))"', "42"),
        ("echo abc | grep b | sed s/abc/xyz/", "xyz"),
        ("for i in 1 2 3; do printf $i; done", "123"),
        ('test -n "$BASH_VERSION" && echo is-bash', "is-bash"),
    ],
)
def test_execute_bash_has_bash_semantics(host_workspace, command, expected):
    result = asyncio.run(
        _invoke(bash_tool.execute_bash, host_workspace.root, command=command)
    )
    output = str(result.llm_output)
    assert "Exit code: 0" in output, output
    assert expected in output, output
    assert "'a b'" not in output


def test_framework_plumbing_runs_in_workspace(host_workspace):
    async def run():
        made = await host_workspace.exec(
            "mkdir -p nested/dir && test -d nested/dir && echo ok"
        )
        assert made.exit_code == 0 and "ok" in made.stdout, made
        await _invoke(
            file_tools.write_file,
            host_workspace.root,
            file_path="nested/dir/note.txt",
            content="hello\n",
            append=False,
        )
        cat = await host_workspace.exec("cat nested/dir/note.txt")
        assert cat.stdout.strip() == "hello", cat

    asyncio.run(run())


def test_background_job_writes_its_log(host_workspace):
    async def run():
        result = await _invoke(
            bash_tool.execute_bash,
            host_workspace.root,
            command="echo bg-done",
            background=True,
        )
        log_path = result.ui_metadata["metadata"]["log_path"]
        job_id = result.ui_metadata["metadata"]["job_id"]
        deadline = time.monotonic() + 20
        content = ""
        while time.monotonic() < deadline:
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if "bg-done" in content:
                    break
            await asyncio.sleep(0.2)
        job = _bash_jobs.get_registry().get(job_id)
        if job is not None and job.poll_returncode() is None:
            await job.handle.terminate()
        assert "bg-done" in content, (log_path, content)

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash paths are Windows-only")
def test_file_tools_accept_git_bash_paths(host_workspace):
    async def run():
        await host_workspace.write_text("from_bash.txt", "via msys path\n")
        pwd = await host_workspace.exec("pwd")
        msys_path = pwd.stdout.strip() + "/from_bash.txt"
        assert msys_path.startswith("/"), msys_path
        result = await _invoke(
            file_tools.read_file, host_workspace.root, file_path=msys_path
        )
        assert "via msys path" in str(result.llm_output)

    asyncio.run(run())
