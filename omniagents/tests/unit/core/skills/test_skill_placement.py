"""Skill folders outside a sandbox Run's workspace are copied into it, read-only.

The sandbox session here runs its commands on this machine, and the
read-only root is a temporary folder, so the copy, read and refusal paths
run for real.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.run_context import RunContextWrapper
from agents.tool_context import ToolContext

import omniagents.workspace.sandbox as sandbox
from omniagents.core.agents.builder import _bind_agent_skill_roots
from omniagents.core.skills import agent_skill_roots, skill_dir_location
from omniagents.execution import bind_request_context, reset_request_context
from omniagents.testing import _direct_invocation_context
from omniagents.workspace.sandbox import SandboxWorkspace


class _LocalSession:
    """A sandbox session whose commands run here."""

    def __init__(self, root):
        self.state = SimpleNamespace(manifest=SimpleNamespace(root=str(root)))
        self.execs = 0

    async def exec(self, *cmd, shell=True, user=None, timeout=None):
        self.execs += 1
        proc = subprocess.run(list(cmd), capture_output=True)
        return SimpleNamespace(
            exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
        )


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A sandbox Run's workspace, bound as the current Run's environment."""
    monkeypatch.setattr(sandbox, "PROVIDED_ROOT", str(tmp_path / "opt"))
    (tmp_path / "ws").mkdir()
    session = _LocalSession(tmp_path / "ws")
    workspace = SandboxWorkspace(session, root=str(tmp_path / "ws"))
    token = bind_request_context(_direct_invocation_context(workspace))
    yield SimpleNamespace(workspace=workspace, session=session, tmp=tmp_path)
    reset_request_context(token)


def _skill(tmp_path):
    skill = tmp_path / "project/skills/playwright-cli"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: playwright-cli\ndescription: Drive a browser.\n---\n\n"
        "See [references/tracing.md](references/tracing.md).\n"
    )
    (skill / "references/tracing.md").write_text("Record a trace.\n")
    return skill


def test_load_skill_points_at_a_readable_copy(run):
    from omniagents.builtin.tools.skill_tool import load_skill

    skill = _skill(run.tmp)
    args = json.dumps({"name": "playwright-cli"})
    ctx = ToolContext(
        context={"workspace_root": run.workspace.root},
        tool_name="load_skill",
        tool_call_id="c1",
        tool_arguments=args,
    )
    with agent_skill_roots([skill.parent]):
        out = asyncio.run(load_skill.on_invoke_tool(ctx, args))
    assert "references/tracing.md" in str(out)
    copy = skill_dir_location(skill)
    assert copy.startswith(sandbox.PROVIDED_ROOT + "/skills/")
    assert copy.endswith("/playwright-cli")
    # The file the skill points to resolves where the agent's tools work.
    text = asyncio.run(run.workspace.read_text(f"{copy}/references/tracing.md"))
    assert text == "Record a trace.\n"
    names = [e.name for e in asyncio.run(run.workspace.list_dir(copy))]
    assert names == ["SKILL.md", "references"]


def test_the_copy_is_read_only_and_follows_the_host(run):
    skill = _skill(run.tmp)
    copy = asyncio.run(run.workspace.provide(skill, "skills/x/playwright-cli"))
    with pytest.raises(PermissionError, match="read-only"):
        asyncio.run(run.workspace.write_text(f"{copy}/SKILL.md", "changed"))
    with pytest.raises(PermissionError):
        asyncio.run(run.workspace.remove(f"{copy}/references", recursive=True))

    execs = run.session.execs
    asyncio.run(run.workspace.provide(skill, "skills/x/playwright-cli"))
    assert run.session.execs == execs  # unchanged on the host: not copied again

    (skill / "references/tracing.md").write_text("Record a trace, then view it.\n")
    asyncio.run(run.workspace.provide(skill, "skills/x/playwright-cli"))
    text = asyncio.run(run.workspace.read_text(f"{copy}/references/tracing.md"))
    assert text == "Record a trace, then view it.\n"


def test_paths_elsewhere_outside_the_workspace_stay_refused(run):
    from omniagents.workspace.types import WorkspaceEscapeError

    with pytest.raises(WorkspaceEscapeError):
        run.workspace.resolve(str(run.tmp / "project"))


def test_instructions_list_the_copies(run):
    skill = _skill(run.tmp)
    spec = SimpleNamespace(skill_roots=[str(skill.parent)])

    async def render(wrapper, agent):
        return wrapper.context["available_skills_block"]

    instructions, _ = _bind_agent_skill_roots(spec, render, [])
    wrapper = RunContextWrapper(
        context={"workspace_root": run.workspace.root, "available_skills_block": ""}
    )
    block = asyncio.run(instructions(wrapper, None))
    location = f"{skill_dir_location(skill)}/SKILL.md"
    assert f"<location>\n{location}\n</location>" in block
    assert asyncio.run(run.workspace.read_text(location)).startswith("---")


def test_host_runs_read_skills_where_they_are(tmp_path):
    skill = _skill(tmp_path)
    assert skill_dir_location(skill) == str(skill)


class _ContainerSession:
    """A sandbox whose workspace is /workspace, which this machine doesn't have.

    Paths under /workspace are kept in ``backing``; anything read from the
    host at /workspace would fail, so a skill found there was found through
    the sandbox.
    """

    def __init__(self, backing):
        self.backing = str(backing)
        self.state = SimpleNamespace(manifest=SimpleNamespace(root="/workspace"))

    def _here(self, value):
        return str(value).replace("/workspace", self.backing)

    async def exec(self, *cmd, shell=True, user=None, timeout=None):
        proc = subprocess.run([self._here(c) for c in cmd], capture_output=True)
        out = proc.stdout.replace(self.backing.encode(), b"/workspace")
        return SimpleNamespace(
            exit_code=proc.returncode, stdout=out, stderr=proc.stderr
        )

    async def read(self, path, user=None):
        import io

        return io.BytesIO(open(self._here(path), "rb").read())

    async def ls(self, path, user=None):
        import os

        here = self._here(path)
        return [
            SimpleNamespace(
                path=f"{path}/{name}",
                kind=SimpleNamespace(
                    value="directory" if os.path.isdir(f"{here}/{name}") else "file"
                ),
            )
            for name in sorted(os.listdir(here))
        ]


@pytest.fixture
def container(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROVIDED_ROOT", str(tmp_path / "opt"))
    backing = tmp_path / "container-workspace"
    repo_skill = backing / ".omniagents/skills/release-notes"
    repo_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text(
        "---\nname: release-notes\ndescription: Write the release notes.\n---\n\nSteps.\n"
    )
    broken = backing / ".omniagents/skills/broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter\n")
    assert not Path("/workspace/.omniagents").exists()
    workspace = SandboxWorkspace(_ContainerSession(backing))
    token = bind_request_context(_direct_invocation_context(workspace))
    yield SimpleNamespace(workspace=workspace, tmp=tmp_path)
    reset_request_context(token)


def test_a_repositorys_own_skills_are_listed_from_the_container(container):
    from omniagents.core.skills import run_skills, skill_roots

    host_skill = _skill(container.tmp)
    with agent_skill_roots([host_skill.parent]):
        skills = asyncio.run(run_skills(skill_roots("/workspace")))
    by_name = {s.name: s for s in skills}
    assert (
        by_name["release-notes"].path == "/workspace/.omniagents/skills/release-notes"
    )
    assert by_name["release-notes"].host_dir is None
    assert by_name["playwright-cli"].path == skill_dir_location(host_skill)
    assert "broken" not in by_name  # invalid skills are not offered


def test_skills_rpcs_see_the_container_skills(container):
    from omniagents.builtin.server_functions.skills import skills_list, skills_read

    listed = asyncio.run(skills_list())
    entries = {s["name"]: s for s in listed["skills"]}
    assert (
        entries["release-notes"]["path"]
        == "/workspace/.omniagents/skills/release-notes"
    )
    assert "error" in entries["broken"]  # reported, not hidden
    read = asyncio.run(skills_read("release-notes"))
    assert read["ok"] and "Steps." in read["text"]


def test_instructions_list_the_repositorys_skills(container):
    spec = SimpleNamespace(skill_roots=[])

    async def render(wrapper, agent):
        return wrapper.context["available_skills_block"]

    instructions, _ = _bind_agent_skill_roots(spec, render, [])
    wrapper = RunContextWrapper(
        context={"workspace_root": "/workspace", "available_skills_block": ""}
    )
    block = asyncio.run(instructions(wrapper, None))
    assert "/workspace/.omniagents/skills/release-notes/SKILL.md" in block
