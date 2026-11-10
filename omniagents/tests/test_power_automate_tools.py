import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omniagents.builtin.tools import power_automate as tools
from omniagents.integrations import power_automate_files
from omniagents.integrations.agent_runtimes import AgentRuntimes
from omniagents.workspace.host import HostWorkspace
from .test_power_automate_actions import bridge_context


def test_tool_schemas_only_expose_action_inputs():
    for tool in (
        tools.power_automate_request,
        tools.power_automate_status,
        tools.power_automate_download_attachment,
    ):
        schema = json.dumps(tool.params_json_schema)
        assert not any(
            key in schema
            for key in ('"source"', '"session_id"', '"directory"', '"agent"')
        )


def test_no_run_fails_closed(monkeypatch):
    monkeypatch.setattr(tools, "current_service", lambda: None)
    monkeypatch.setattr(tools, "current_session_id", lambda: None)
    with pytest.raises(ValueError):
        tools.bridge_context()


def test_tools_bind_actual_service_and_session(monkeypatch):
    backend = object()
    monkeypatch.setattr(
        tools,
        "current_service",
        lambda: SimpleNamespace(
            _power_automate=backend, _power_automate_agent="assistant"
        ),
    )
    monkeypatch.setattr(tools, "current_session_id", lambda: "session")
    assert tools.bridge_context() == (backend, "assistant", "session")


@pytest.mark.asyncio
async def test_child_service_inherits_bridge():
    backend = object()
    primary = SimpleNamespace(
        state=SimpleNamespace(agent_service=SimpleNamespace(_power_automate=backend))
    )
    child = SimpleNamespace(state=SimpleNamespace(agent_service=SimpleNamespace()))
    runtimes = AgentRuntimes(
        SimpleNamespace(entrypoint="primary", agents_map={"child": object()}),
        auth_token=None,
        primary_app=primary,
    )
    runtimes._apps["child"] = child
    await runtimes.app("child")
    assert child.state.agent_service._power_automate is backend
    assert child.state.agent_service._power_automate_agent == "child"


@pytest.mark.asyncio
async def test_host_stream_never_overwrites(tmp_path):
    workspace = HostWorkspace(tmp_path)
    await workspace.write_stream("file", io.BytesIO(b"original"))
    with pytest.raises(FileExistsError):
        await workspace.write_stream("file", io.BytesIO(b"replacement"))
    assert (tmp_path / "file").read_bytes() == b"original"


@pytest.mark.asyncio
async def test_host_stream_cleans_failed_copy(tmp_path):
    class BrokenStream:
        def read(self, size):
            raise OSError("failed")

    workspace = HostWorkspace(tmp_path)
    with pytest.raises(OSError):
        await workspace.write_stream("file", BrokenStream())
    assert not (tmp_path / "file").exists()


@pytest.mark.asyncio
async def test_attachment_policy_and_session(tmp_path):
    actions = bridge_context(tmp_path)
    try:
        with pytest.raises(ValueError, match="no_bridge_conversation"):
            await actions.authorize_attachment("assistant", "other", str(uuid4()))
        actions.source.config.sources["intake"]["agent_access"]["assistant"][
            "download_attachments"
        ] = False
        with pytest.raises(ValueError, match="attachments_not_authorized"):
            await actions.authorize_attachment("assistant", "session", str(uuid4()))
    finally:
        actions.event_store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_workspace_download_and_cleanup(tmp_path, monkeypatch, failed):
    actions = bridge_context(tmp_path)
    identity = str(uuid4())
    actions.authorize_attachment = AsyncMock(return_value=("intake", identity))
    monkeypatch.setenv(actions.source.config.connection_env, "test-dsn")
    staging_paths = []

    def fetch(dsn, attachment_id, source, directory):
        assert source == "intake" and attachment_id == identity
        staged = Path(directory) / identity
        staged.write_bytes(b"verified file content")
        staging_paths.append(staged)
        return staged

    monkeypatch.setattr(power_automate_files, "fetch_attachment", fetch)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = HostWorkspace(root)
    if failed:
        workspace.write_stream = AsyncMock(side_effect=OSError("failed transfer"))
    try:
        if failed:
            with pytest.raises(OSError):
                await actions.download("assistant", "session", identity, workspace)
            assert list(root.iterdir()) == []
        else:
            result = await actions.download("assistant", "session", identity, workspace)
            assert Path(result["path"]).read_bytes() == b"verified file content"
        actions.authorize_attachment.assert_awaited_once_with(
            "assistant", "session", identity
        )
        assert not staging_paths[0].exists()
    finally:
        actions.event_store.close()


@pytest.mark.asyncio
async def test_sandbox_stream_passes_file_object_without_buffering():
    from omniagents.workspace.sandbox import SandboxWorkspace

    session = SimpleNamespace(write=AsyncMock())
    workspace = SandboxWorkspace(session, root="/workspace")
    workspace.exists = AsyncMock(return_value=False)
    stream = io.BytesIO(b"contents")
    await workspace.write_stream("download", stream)
    assert session.write.call_args.args[1] is stream
    workspace.exists = AsyncMock(return_value=True)
    with pytest.raises(FileExistsError):
        await workspace.write_stream("download", stream)
    assert session.write.await_count == 1
