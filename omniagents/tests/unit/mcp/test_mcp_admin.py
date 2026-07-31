"""Unit tests for the MCP management registry (McpServerManager).

Covers provenance/read-only rules, the layered gates, validated CRUD
with write-only secret channels, adversarial secret read-back across
every read path, status transitions + broadcast semantics, and typed
failure classification.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from omniagents.core.agents.mcp_admin import (
    McpConfigInvalid,
    McpServerManager,
    McpServerNotFound,
    McpServerReadOnly,
    classify_mcp_failure,
    redact_server_params,
)
from omniagents.core.agents.mcp_oauth import McpInteractiveAuthRequired
from omniagents.core.agents.specs import MCPServerConfig

HEADER_SECRET = "Bearer HEADER-SECRET-abc123"
ENV_SECRET = "ENV-SECRET-def456"
NEW_SECRET = "NEW-SECRET-xyz789"

PRODUCT = "mcptestprod"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated product config dir + project root with a manifest."""
    config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = project_root / "project.yml"
    manifest.write_text(
        f"name: {PRODUCT}\nallow_user_mcp_servers: true\n", encoding="utf-8"
    )
    return SimpleNamespace(
        config_home=config_home,
        project_root=project_root,
        manifest=manifest,
        user_mcp=config_home / PRODUCT / "mcp.json",
        project_local=project_root / f".{PRODUCT}" / "mcp.json",
    )


def _spec(env, mcp_servers=None):
    return SimpleNamespace(
        mcp_servers=list(mcp_servers or []),
        project_slug=PRODUCT,
        manifest_path=str(env.manifest),
    )


def _project_server():
    return MCPServerConfig(
        name="proj-http",
        type="streamable_http",
        params={
            "url": "http://example.test/mcp",
            "headers": {"Authorization": HEADER_SECRET},
        },
    )


def _write_mcp_json(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _manager(env, spec=None, broadcast=None):
    return McpServerManager(
        spec if spec is not None else _spec(env),
        broadcast=broadcast,
    )


def _assert_no_secret(payload, *secrets):
    text = json.dumps(payload, default=str)
    for secret in secrets:
        assert secret not in text, f"secret {secret!r} leaked in {text[:400]}"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redaction_masks_header_and_env_values_only():
    redacted = redact_server_params(
        {
            "url": "http://example.test/mcp",
            "headers": {"Authorization": HEADER_SECRET, "X-Trace": "on"},
            "command": "npx",
            "args": ["server", "--flag"],
            "env": {"API_KEY": ENV_SECRET},
            "auth": object(),
        }
    )
    assert redacted["url"] == "http://example.test/mcp"
    assert redacted["command"] == "npx"
    assert redacted["args"] == ["server", "--flag"]
    assert redacted["headers"] == {
        "Authorization": {"is_set": True},
        "X-Trace": {"is_set": True},
    }
    assert redacted["env"] == {"API_KEY": {"is_set": True}}
    assert "auth" not in redacted
    _assert_no_secret(redacted, HEADER_SECRET, ENV_SECRET)


# ---------------------------------------------------------------------------
# Provenance & listing
# ---------------------------------------------------------------------------


def test_sources_and_read_only_rules(env):
    _write_mcp_json(
        env.user_mcp,
        {"user-srv": {"type": "stdio", "command": "echo", "env": {"K": ENV_SECRET}}},
    )
    _write_mcp_json(
        env.project_local,
        {"local-srv": {"type": "streamable_http", "url": "http://local.test/mcp"}},
    )
    manager = _manager(env, _spec(env, [_project_server()]))
    listing = manager.list_servers()
    by_name = {item["server_name"]: item for item in listing["servers"]}

    assert by_name["proj-http"]["source"] == "project"
    assert by_name["proj-http"]["read_only"] is True
    assert by_name["proj-http"]["read_only_reason"] == "project_manifest"
    assert by_name["user-srv"]["source"] == "user"
    assert by_name["user-srv"]["read_only"] is False
    assert by_name["local-srv"]["source"] == "project_local"
    assert by_name["local-srv"]["read_only_reason"] == "project_local_file"
    assert listing["user_mcp_allowed"] is True
    assert listing["write_target"] == str(env.user_mcp)
    _assert_no_secret(listing, HEADER_SECRET, ENV_SECRET)


def test_project_local_wins_over_user_global(env):
    _write_mcp_json(env.user_mcp, {"dup": {"type": "stdio", "command": "user-cmd"}})
    _write_mcp_json(
        env.project_local,
        {"dup": {"type": "streamable_http", "url": "http://local.test/mcp"}},
    )
    manager = _manager(env)
    snapshot = manager.get_server("dup")
    assert snapshot["source"] == "project_local"
    assert snapshot["transport"] == "streamable_http"


def test_user_mcp_disabled_gate(env):
    env.manifest.write_text(f"name: {PRODUCT}\n", encoding="utf-8")  # default False
    _write_mcp_json(env.user_mcp, {"user-srv": {"type": "stdio", "command": "echo"}})
    manager = _manager(env)
    listing = manager.list_servers()
    by_name = {item["server_name"]: item for item in listing["servers"]}
    assert listing["user_mcp_allowed"] is False
    assert by_name["user-srv"]["enabled"] is False
    assert by_name["user-srv"]["disabled_reason"] == "user_mcp_disabled"
    with pytest.raises(McpServerReadOnly) as excinfo:
        manager.create_server("new-srv", "stdio", {"command": "echo"})
    assert excinfo.value.reason == "user_mcp_disabled"
    # Effective configs exclude the gated-out server.
    assert "user-srv" not in [cfg.name for cfg in manager.effective_configs()]


def test_allowlist_gate_disables_unlisted_servers(env):
    env.manifest.write_text(
        f"name: {PRODUCT}\n"
        "allow_user_mcp_servers: true\n"
        "security:\n"
        "  mcp_require_allowlist: true\n"
        "  mcp_allowed_servers: [proj-http]\n",
        encoding="utf-8",
    )
    spec = _spec(
        env, [_project_server(), MCPServerConfig("other", "stdio", {"command": "x"})]
    )
    manager = _manager(env, spec)
    by_name = {item["server_name"]: item for item in manager.list_servers()["servers"]}
    assert by_name["proj-http"]["enabled"] is True
    assert by_name["other"]["enabled"] is False
    assert by_name["other"]["disabled_reason"] == "allowlist_blocked"
    assert [cfg.name for cfg in manager.effective_configs()] == ["proj-http"]


def test_kill_switch_marks_disabled(env):
    manager = _manager(env, _spec(env, [_project_server()]))
    manager.mark_disabled("proj-http")
    snapshot = manager.get_server("proj-http")
    assert snapshot["enabled"] is False
    assert snapshot["disabled_reason"] == "kill_switch"
    assert snapshot["status"]["state"] == "disabled"
    assert manager.effective_configs() == []


def test_session_server_snapshot_redacts(env):
    manager = _manager(env)
    snapshot = manager.session_server_snapshot(
        {
            "name": "app-srv",
            "type": "streamable_http",
            "params": {
                "url": "http://app.test/mcp",
                "headers": {"Authorization": HEADER_SECRET},
            },
        }
    )
    assert snapshot["source"] == "session"
    assert snapshot["read_only_reason"] == "session_scoped"
    _assert_no_secret(snapshot, HEADER_SECRET)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_validates_and_never_echoes_secrets(env):
    manager = _manager(env)
    with pytest.raises(McpConfigInvalid) as excinfo:
        manager.create_server(
            "bad",
            "warp",  # invalid transport
            {"headers": {"Authorization": HEADER_SECRET}},
        )
    _assert_no_secret(excinfo.value.data, HEADER_SECRET)

    with pytest.raises(McpConfigInvalid) as excinfo:
        manager.create_server(
            "bad",
            "streamable_http",
            {"headers": {"Authorization": HEADER_SECRET}},  # missing url
        )
    codes = {error["code"] for error in excinfo.value.errors}
    assert "url_required" in codes
    _assert_no_secret(excinfo.value.data, HEADER_SECRET)

    with pytest.raises(McpConfigInvalid) as excinfo:
        manager.create_server("bad", "stdio", {"command": "echo", "bogus": 1})
    assert {error["code"] for error in excinfo.value.errors} == {"unknown_param"}


def test_create_get_delete_roundtrip_with_secret_writeonly(env):
    spec = _spec(env)
    manager = _manager(env, spec)
    result = manager.create_server(
        "hub",
        "streamable_http",
        {"url": "http://hub.test/mcp", "headers": {"Authorization": HEADER_SECRET}},
        {"cache_tools_list": True},
    )
    assert result["ok"] is True
    _assert_no_secret(result, HEADER_SECRET)

    # Written to the user-global mcp.json with restrictive permissions...
    assert env.user_mcp.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(env.user_mcp.stat().st_mode) == 0o600
    stored = json.loads(env.user_mcp.read_text())
    assert stored["mcpServers"]["hub"]["headers"]["Authorization"] == HEADER_SECRET

    # ...applied to the live spec for the next session build...
    assert "hub" in [cfg.name for cfg in spec.mcp_servers]

    # ...and never readable back through any read path.
    _assert_no_secret(manager.get_server("hub"), HEADER_SECRET)
    _assert_no_secret(manager.list_servers(), HEADER_SECRET)

    deleted = manager.delete_server("hub")
    assert deleted["ok"] is True
    assert "hub" not in json.loads(env.user_mcp.read_text())["mcpServers"]
    assert "hub" not in [cfg.name for cfg in spec.mcp_servers]
    with pytest.raises(McpServerNotFound):
        manager.get_server("hub")


def test_create_name_conflict_and_missing_update_target(env):
    manager = _manager(env, _spec(env, [_project_server()]))
    with pytest.raises(McpConfigInvalid) as excinfo:
        manager.create_server("proj-http", "streamable_http", {"url": "http://x.test"})
    assert excinfo.value.errors[0]["code"] == "name_conflict"
    with pytest.raises(McpServerNotFound):
        manager.update_server("ghost", params={"url": "http://x.test"})
    with pytest.raises(McpServerReadOnly) as read_only:
        manager.update_server("proj-http", params={"url": "http://x.test"})
    assert read_only.value.reason == "project_manifest"
    with pytest.raises(McpServerReadOnly):
        manager.delete_server("proj-http")


def test_update_merges_secret_channels_write_only(env):
    manager = _manager(env)
    manager.create_server(
        "hub",
        "streamable_http",
        {
            "url": "http://hub.test/mcp",
            "headers": {"Authorization": HEADER_SECRET, "X-Keep": "keep-me"},
        },
    )
    # A client round-trips the redacted read and only adds a new header:
    # omitted names keep their stored values, null deletes, string sets.
    result = manager.update_server(
        "hub",
        params={"headers": {"X-New": NEW_SECRET, "X-Keep": None}},
    )
    _assert_no_secret(result, HEADER_SECRET, NEW_SECRET)
    stored = json.loads(env.user_mcp.read_text())["mcpServers"]["hub"]
    assert stored["headers"]["Authorization"] == HEADER_SECRET  # kept
    assert stored["headers"]["X-New"] == NEW_SECRET  # set
    assert "X-Keep" not in stored["headers"]  # deleted

    # Non-secret params replace wholesale.
    manager.update_server("hub", params={"url": "http://hub2.test/mcp"})
    stored = json.loads(env.user_mcp.read_text())["mcpServers"]["hub"]
    assert stored["url"] == "http://hub2.test/mcp"
    assert stored["headers"]["Authorization"] == HEADER_SECRET

    # A transport change resets params wholesale (old url invalid).
    with pytest.raises(McpConfigInvalid):
        manager.update_server("hub", server_type="stdio", params={})
    manager.update_server("hub", server_type="stdio", params={"command": "echo"})
    stored = json.loads(env.user_mcp.read_text())["mcpServers"]["hub"]
    assert stored["type"] == "stdio"
    assert "url" not in stored


# ---------------------------------------------------------------------------
# Status transitions & broadcast
# ---------------------------------------------------------------------------


def test_transitions_broadcast_and_are_queryable(env):
    events = []

    async def broadcast(payload):
        events.append(payload)

    manager = _manager(env, _spec(env, [_project_server()]), broadcast=broadcast)

    async def scenario():
        await manager.record_transition("proj-http", "starting", origin="probe")
        await manager.record_transition("proj-http", "ready", origin="probe")
        # Unchanged non-failed state: recorded, not rebroadcast.
        await manager.record_transition("proj-http", "ready", origin="probe")

    asyncio.run(scenario())
    assert [event["status"] for event in events] == ["starting", "ready"]
    assert events[1]["previous_status"] == "starting"
    assert manager.status_of("proj-http")["state"] == "ready"
    assert manager.status_of("proj-http")["origin"] == "probe"


def test_record_failure_classifies_and_broadcasts_reason(env):
    events = []

    async def broadcast(payload):
        events.append(payload)

    manager = _manager(env, _spec(env, [_project_server()]), broadcast=broadcast)

    async def scenario():
        await manager.record_failure(
            "proj-http",
            McpInteractiveAuthRequired("proj-http", had_tokens=True),
            origin="session:abc",
        )

    asyncio.run(scenario())
    assert events[-1]["status"] == "failed"
    assert events[-1]["reason_code"] == "auth_expired"
    assert events[-1]["auth_state"] == "expired"
    status = manager.status_of("proj-http")
    assert status["state"] == "failed"
    assert status["reason_code"] == "auth_expired"


def test_failure_classification_matrix():
    assert classify_mcp_failure(FileNotFoundError(2, "no", "npx"))[0] == (
        "command_not_found"
    )
    assert classify_mcp_failure(asyncio.TimeoutError())[0] == "timeout"
    assert classify_mcp_failure(ValueError("requires a url"))[0] == "invalid_config"
    assert (
        classify_mcp_failure(McpInteractiveAuthRequired("s", had_tokens=False))[0]
        == "auth_required"
    )
    assert (
        classify_mcp_failure(RuntimeError("Connection refused by host"))[0]
        == "connect_failed"
    )
    # Wrapped in an ExceptionGroup (anyio task groups do this).
    grouped = BaseExceptionGroup("boom", [FileNotFoundError(2, "no", "uvx")])
    assert classify_mcp_failure(grouped)[0] == "command_not_found"


def test_auth_state_kinds(env):
    _write_mcp_json(
        env.user_mcp,
        {
            "stdio-env": {
                "type": "stdio",
                "command": "echo",
                "env": {"KEY": ENV_SECRET},
            },
            "http-hdr": {
                "type": "streamable_http",
                "url": "http://h.test/mcp",
                "headers": {"Authorization": HEADER_SECRET},
            },
            "http-bare": {"type": "streamable_http", "url": "http://b.test/mcp"},
        },
    )
    manager = _manager(env)
    assert manager.auth_state("stdio-env") == {"kind": "env", "state": "configured"}
    assert manager.auth_state("http-hdr") == {
        "kind": "headers",
        "state": "configured",
    }
    assert manager.auth_state("http-bare") == {
        "kind": "none",
        "state": "not_authenticated",
    }
    # Explicit Authorization headers suppress OAuth injection.
    assert manager.auth_resolver(manager.require_config("http-hdr")) is None
    assert manager.auth_resolver(manager.require_config("stdio-env")) is None
    assert manager.auth_resolver(manager.require_config("http-bare")) is not None
