import json
import types
from contextlib import contextmanager
from pathlib import PurePosixPath

import pytest

from omniagents.builtin.tools import skill_tool
from omniagents.core.skills import set_skill_roots_provider
from omniagents.execution import (
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    WorkspaceResource,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@pytest.fixture(autouse=True)
def _clear_provider():
    set_skill_roots_provider(None)
    yield
    set_skill_roots_provider(None)


def _ctx(workspace_root):
    return types.SimpleNamespace(context={"workspace_root": str(workspace_root)})


def _write_skill(root, name, body="Body\n"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(f"---\nname: {name}\ndescription: test\n---\n\n{body}")
    return p


class _MemoryWorkspace:
    root = "/workspace"

    def __init__(self, files):
        self.files = dict(files)
        self.read_paths = []

    async def read_text(self, path, *, encoding="utf-8"):
        del encoding
        self.read_paths.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def resolve(self, path):
        return str(PurePosixPath(self.root) / path)


@contextmanager
def _routed_devbox(workspace, sources):
    resource = WorkspaceResource("workspace-1", sources=tuple(sources))
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="environment-1",
        workspace_id=resource.workspace_id,
        kind=EnvironmentKind.CONTAINER,
        state=EnvironmentState.READY,
        generation=1,
    )
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="load_skill",
                resource_scope=ResourceScope.RUN,
                execution_requirements=ExecutionRequirement.WORKSPACE_READ,
            ),
            principal=None,
            workspace=resource,
            environment=descriptor,
            environment_lease=EnvironmentLease(descriptor, workspace),
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@pytest.mark.asyncio
async def test_load_skill_default_root(tmp_path):
    default_root = tmp_path / ".omniagents" / "skills"
    _write_skill(default_root, "my-skill", body="Do the thing.\n")

    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "my-skill")
    assert "Do the thing." in str(out)
    assert out.ui_metadata["metadata"]["success"] is True


@pytest.mark.asyncio
async def test_load_skill_not_found_lists_searched_paths(tmp_path):
    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "missing")
    text = str(out)
    assert "not found" in text
    assert ".omniagents" in text
    assert out.ui_metadata["metadata"]["success"] is False


@pytest.mark.asyncio
async def test_load_skill_reads_devbox_source_through_environment_workspace(tmp_path):
    host_source = tmp_path / "host-source"
    _write_skill(host_source / "skills", "mounted-skill", body="wrong host copy\n")
    workspace = _MemoryWorkspace(
        {
            "repo/skills/mounted-skill/SKILL.md": (
                "---\nname: mounted-skill\ndescription: test\n---\n\n"
                "right devbox copy\n"
            )
        }
    )
    sources = [
        {
            "kind": "local-git",
            "mountName": "repo",
            "path": str(host_source),
        }
    ]

    with _routed_devbox(workspace, sources):
        out = await skill_tool.load_skill._original_func(
            _ctx("/workspace"), "mounted-skill"
        )

    assert "right devbox copy" in str(out)
    assert "wrong host copy" not in str(out)
    assert "repo/skills/mounted-skill/SKILL.md" in workspace.read_paths
    assert out.ui_metadata["metadata"]["skill_path"] == (
        "/workspace/repo/skills/mounted-skill/SKILL.md"
    )


@pytest.mark.asyncio
async def test_load_skill_uses_installed_provider(tmp_path):
    custom_root = tmp_path / "custom" / "skills"
    _write_skill(custom_root, "provided-skill", body="Provider wins.\n")
    set_skill_roots_provider(lambda workspace_root: [custom_root])

    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "provided-skill")
    assert "Provider wins." in str(out)


@pytest.mark.asyncio
async def test_provider_order_earlier_root_wins(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_skill(first, "dup", body="from first\n")
    _write_skill(second, "dup", body="from second\n")
    set_skill_roots_provider(lambda workspace_root: [first, second])

    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "dup")
    assert "from first" in str(out)


def test_failing_provider_degrades_to_default(tmp_path):
    from omniagents.core.skills import skill_roots

    def boom(workspace_root):
        raise RuntimeError("boom")

    set_skill_roots_provider(boom)
    roots = skill_roots(str(tmp_path))
    # Derived default: workspace dot-dir first, per-user global root last.
    assert roots[0] == tmp_path / ".omniagents" / "skills"
    assert roots[-1].name == "skills"
    assert roots[-1].parent.name == "omniagents"


@pytest.mark.asyncio
async def test_global_skill_reference_readable_from_composite_host(tmp_path):
    from omniagents.workspace import MountedHostWorkspace
    from omniagents.builtin.tools.file_tools import read_file

    global_root = tmp_path / "global-skills"
    skill = _write_skill(global_root, "comms", "Read examples/general.md")
    reference = skill.parent / "examples" / "general.md"
    reference.parent.mkdir()
    reference.write_text("Supporting instructions")
    source = tmp_path / "project"
    source.mkdir()
    workspace = MountedHostWorkspace(
        str(tmp_path / "logical"),
        [{"kind": "local", "mountName": "project", "path": str(source)}],
    )
    set_skill_roots_provider(lambda *_: [global_root])
    with _routed_devbox(workspace, []):
        loaded = await skill_tool.load_skill._original_func(
            _ctx(workspace.root), "comms"
        )
        assert "Read examples/general.md" in str(loaded)
        result = await read_file._original_func(_ctx(workspace.root), str(reference))
        assert "Supporting instructions" in str(result)
        assert "Error reading file" not in str(result)
