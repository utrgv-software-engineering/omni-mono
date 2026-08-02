"""End-to-end ``fs_*`` RPC tests over the real /ws surface (tkt_B5fXOZHf08K1).

Exercises the ticket's acceptance criteria on the wire:

* workspace changes reach a subscribed client as pushed ``fs_events``
  (the client only blocks on its socket — no polling requests),
* overflow surfaces as an explicit ``fs_rescan_required`` event,
* large transfers ride chunked frames (never one full-payload frame),
  round-trip with sha256 verification, progress events, and mid-transfer
  cancellation,
* unauthorized paths are rejected consistently (relative traversal,
  absolute paths, symlink escapes),
* cross-user access to another principal's workspace answers -32014,
* experimental gating (-32013) and reconnect re-subscribe semantics,
* fs notifications never enter the durable replay journal.
"""

from __future__ import annotations

import base64
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.execution import AgentHostResources, register_host_environment
from tests.fixtures.gui_ws import perform_gui_handshake

from agents import Agent

FS_EXPERIMENTAL = (
    "fs_watch",
    "fs_unwatch",
    "fs_list",
    "fs_stat",
    "fs_download_open",
    "fs_download_read",
    "fs_download_close",
    "fs_upload_open",
    "fs_upload_chunk",
    "fs_upload_commit",
    "fs_upload_abort",
    "fs_events",
    "fs_rescan_required",
    "fs_transfer_progress",
)


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="fs", tools=[], instructions="", model="gpt")


def _spec(security_config=None) -> AgentSpec:
    return AgentSpec(
        name="FsAgent",
        welcome_text="hi",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
        security_config=security_config,
    )


def _call(ws, call_id, method, params=None):
    ws.send_json(
        {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params or {}}
    )


def _result(ws, call_id):
    frames = []
    while True:
        message = ws.receive_json()
        frames.append(message)
        if message.get("id") == call_id:
            return message, frames


def _ok(ws, call_id, method, params=None):
    _call(ws, call_id, method, params)
    message, frames = _result(ws, call_id)
    assert "result" in message, f"{method} failed: {message}"
    return message["result"], frames


def _error(ws, call_id, method, params=None):
    _call(ws, call_id, method, params)
    message, _ = _result(ws, call_id)
    assert "error" in message, f"{method} unexpectedly succeeded: {message}"
    return message["error"]


def _wait_for_method(ws, method):
    frames = []
    while True:
        message = ws.receive_json()
        frames.append(message)
        if message.get("method") == method:
            return message, frames


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def client(workspace) -> TestClient:
    resources = AgentHostResources.create("fs-test-host")
    register_host_environment(
        resources,
        str(workspace),
        owner_user_id=None,
        workspace_id="fs-workspace",
        environment_id="fs-env",
    )
    return TestClient(
        server_app.build_app(spec=_spec(), agent_host_resources=resources)
    )


def _ensure_session(ws, session_id, workspace_root, call_id="ensure"):
    result, _ = _ok(
        ws,
        call_id,
        "server_call",
        {
            "function": "session.ensure",
            "args": {
                "session_id": session_id,
                "workspace_root": str(workspace_root),
            },
        },
    )
    return result


def test_fs_methods_require_experimental_grant(client, workspace):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws)
        error = _error(ws, 1, "fs_list", {"environment_id": "fs-env", "path": "."})
        assert error["code"] == -32013
        assert error["data"]["kind"] == "capability_not_negotiated"


def test_watch_pushes_events_without_client_polling(client, workspace):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)
        watch, _ = _ok(
            ws,
            1,
            "fs_watch",
            {"environment_id": "fs-env", "path": ".", "poll_interval_ms": 200},
        )
        assert watch["mechanism"] == "poll:host"
        assert watch["environment_id"] == "fs-env"

        # Server-side change; the client only blocks on its socket.
        (workspace / "hello.txt").write_text("hello")
        message, _ = _wait_for_method(ws, "fs_events")
        events = message["params"]["events"]
        assert {"type": "created", "path": "hello.txt", "entry_type": "file"} in events
        assert message["params"]["watch_id"] == watch["watch_id"]

        # The change stream is per-connection state, not journaled: the
        # durable replay log contains no fs notifications.
        resumed, _ = _ok(ws, 2, "resume_session", {"session_id": "fs-sess"})
        assert not [
            event
            for event in resumed["events"]
            if str(event.get("method", "")).startswith("fs_")
        ]

        result, _ = _ok(
            ws,
            3,
            "fs_unwatch",
            {"environment_id": "fs-env", "watch_id": watch["watch_id"]},
        )
        assert result is True


def test_watch_overflow_emits_rescan_required(client, workspace):
    service = client.app.state.agent_service
    service.filesystem._max_events_per_cycle = 3
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)
        watch, _ = _ok(
            ws,
            1,
            "fs_watch",
            {"environment_id": "fs-env", "path": ".", "poll_interval_ms": 200},
        )
        for index in range(20):
            (workspace / f"burst-{index}.txt").write_text("x")
        message, frames = _wait_for_method(ws, "fs_rescan_required")
        assert message["params"]["reason"] == "event_overflow"
        assert message["params"]["watch_id"] == watch["watch_id"]
        # The burst produced the rescan signal instead of an event flood.
        assert not [frame for frame in frames if frame.get("method") == "fs_events"]
        # Recovery is an explicit relist.
        listing, _ = _ok(ws, 2, "fs_list", {"environment_id": "fs-env", "path": "."})
        assert len(listing["entries"]) == 20


def test_unauthorized_paths_rejected_consistently(client, workspace):
    outside = workspace.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    os.symlink(outside / "secret.txt", workspace / "sneaky.txt")

    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)
        for path in ("../outside/secret.txt", "/etc/passwd", "sneaky.txt"):
            for call_id, method, params in (
                ("a", "fs_stat", {"environment_id": "fs-env", "path": path}),
                ("b", "fs_download_open", {"environment_id": "fs-env", "path": path}),
                (
                    "c",
                    "fs_upload_open",
                    {"environment_id": "fs-env", "path": path, "size": 1},
                ),
                ("d", "fs_watch", {"environment_id": "fs-env", "path": path}),
            ):
                error = _error(ws, call_id, method, params)
                assert error["code"] in (-32060, -32061), (path, method, error)
                if error["code"] == -32060:
                    assert error["data"]["kind"] == "fs_path_invalid"
        # And plainly missing files are a distinct, honest error.
        error = _error(
            ws, 9, "fs_download_open", {"environment_id": "fs-env", "path": "nope.bin"}
        )
        assert error["code"] == -32061
        assert error["data"]["kind"] == "fs_not_found"


def test_chunked_upload_download_round_trip(client, workspace):
    payload = os.urandom(300_000)  # > one 256 KiB chunk
    digest = hashlib.sha256(payload).hexdigest()
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)

        opened, _ = _ok(
            ws,
            1,
            "fs_upload_open",
            {
                "environment_id": "fs-env",
                "path": "artifacts/big.bin",
                "size": len(payload),
                "sha256": digest,
            },
        )
        chunk_size = opened["chunk_size"]
        assert chunk_size < len(payload), "payload must span multiple frames"
        observed_frames = []
        offset = 0
        index = 0
        while offset < len(payload):
            chunk = payload[offset : offset + chunk_size]
            result, frames = _ok(
                ws,
                f"up-{index}",
                "fs_upload_chunk",
                {
                    "environment_id": "fs-env",
                    "transfer_id": opened["transfer_id"],
                    "offset": offset,
                    "data": base64.b64encode(chunk).decode(),
                },
            )
            observed_frames.extend(frames)
            offset += len(chunk)
            index += 1
            assert result["received"] == offset
        committed, frames = _ok(
            ws,
            2,
            "fs_upload_commit",
            {"environment_id": "fs-env", "transfer_id": opened["transfer_id"]},
        )
        observed_frames.extend(frames)
        assert committed == {
            "path": "artifacts/big.bin",
            "size": len(payload),
            "sha256": digest,
        }
        assert (workspace / "artifacts" / "big.bin").read_bytes() == payload
        progress = [
            frame
            for frame in observed_frames
            if frame.get("method") == "fs_transfer_progress"
        ]
        assert progress and progress[-1]["params"]["direction"] == "upload"
        assert progress[-1]["params"]["transferred"] == len(payload)

        # Download it back in chunks and verify the advertised checksum.
        download, _ = _ok(
            ws,
            3,
            "fs_download_open",
            {"environment_id": "fs-env", "path": "artifacts/big.bin"},
        )
        assert download["size"] == len(payload)
        assert download["sha256"] == digest
        assert download["mime"] == "application/octet-stream"
        received = b""
        index = 0
        while True:
            chunk_result, _ = _ok(
                ws,
                f"down-{index}",
                "fs_download_read",
                {"environment_id": "fs-env", "transfer_id": download["transfer_id"]},
            )
            # No single frame ever embeds the full payload.
            assert chunk_result["bytes"] <= chunk_size
            received += base64.b64decode(chunk_result["data"])
            index += 1
            if chunk_result["eof"]:
                break
        assert index > 1
        assert received == payload
        assert hashlib.sha256(received).hexdigest() == download["sha256"]
        closed, _ = _ok(
            ws,
            4,
            "fs_download_close",
            {"environment_id": "fs-env", "transfer_id": download["transfer_id"]},
        )
        assert closed is True


def test_upload_cancellation_mid_transfer(client, workspace):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)
        opened, _ = _ok(
            ws,
            1,
            "fs_upload_open",
            {"environment_id": "fs-env", "path": "cancel.bin", "size": 10},
        )
        _ok(
            ws,
            2,
            "fs_upload_chunk",
            {
                "environment_id": "fs-env",
                "transfer_id": opened["transfer_id"],
                "offset": 0,
                "data": base64.b64encode(b"12345").decode(),
            },
        )
        aborted, _ = _ok(
            ws,
            3,
            "fs_upload_abort",
            {"environment_id": "fs-env", "transfer_id": opened["transfer_id"]},
        )
        assert aborted is True
        error = _error(
            ws,
            4,
            "fs_upload_commit",
            {"environment_id": "fs-env", "transfer_id": opened["transfer_id"]},
        )
        assert error["code"] == -32062
        assert error["data"]["kind"] == "fs_transfer_invalid"
        assert not (workspace / "cancel.bin").exists()


def test_reconnect_requires_resubscribe_and_rescan(client, workspace):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)
        watch, _ = _ok(
            ws,
            1,
            "fs_watch",
            {"environment_id": "fs-env", "path": ".", "poll_interval_ms": 200},
        )
        watch_id = watch["watch_id"]

    # Connection dropped: the watch died with it. A change made while
    # disconnected produces no queued events anywhere.
    (workspace / "while-offline.txt").write_text("x")

    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=FS_EXPERIMENTAL)
        _ensure_session(ws, "fs-sess", workspace)
        error = _error(
            ws, 1, "fs_unwatch", {"environment_id": "fs-env", "watch_id": watch_id}
        )
        assert error["code"] == -32063
        # Documented recovery: rescan, then re-subscribe.
        listing, _ = _ok(ws, 2, "fs_list", {"environment_id": "fs-env", "path": "."})
        assert any(entry["path"] == "while-offline.txt" for entry in listing["entries"])
        rewatch, _ = _ok(
            ws,
            3,
            "fs_watch",
            {"environment_id": "fs-env", "path": ".", "poll_interval_ms": 200},
        )
        (workspace / "after-reconnect.txt").write_text("y")
        message, _ = _wait_for_method(ws, "fs_events")
        assert any(
            event["path"] == "after-reconnect.txt"
            for event in message["params"]["events"]
        )
        _ok(
            ws,
            4,
            "fs_unwatch",
            {"environment_id": "fs-env", "watch_id": rewatch["watch_id"]},
        )


def test_cross_user_workspace_access_denied(tmp_path):
    workspace = tmp_path / "alice-workspace"
    workspace.mkdir()
    (workspace / "private.txt").write_text("alice only")
    spec = _spec(
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        }
    )
    resources = AgentHostResources.create("fs-auth-test-host")
    register_host_environment(
        resources,
        str(workspace),
        owner_user_id="alice",
        workspace_id="alice-workspace",
        environment_id="alice-env",
    )
    client = TestClient(server_app.build_app(spec=spec, agent_host_resources=resources))
    alice_headers = {"Authorization": "Bearer alice-token"}
    bob_headers = {"Authorization": "Bearer bob-token"}
    with (
        client.websocket_connect("/ws", headers=alice_headers) as alice,
        client.websocket_connect("/ws", headers=bob_headers) as bob,
    ):
        perform_gui_handshake(alice, experimental=FS_EXPERIMENTAL)
        perform_gui_handshake(bob, experimental=FS_EXPERIMENTAL)
        _ensure_session(alice, "alice-sess", workspace)

        # Alice can use her own workspace.
        listing, _ = _ok(
            alice, 1, "fs_list", {"environment_id": "alice-env", "path": "."}
        )
        assert any(entry["path"] == "private.txt" for entry in listing["entries"])

        # Bob cannot watch, list, stat, download from, or upload into it.
        for call_id, method, params in (
            ("w", "fs_watch", {"environment_id": "alice-env", "path": "."}),
            ("l", "fs_list", {"environment_id": "alice-env", "path": "."}),
            ("s", "fs_stat", {"environment_id": "alice-env", "path": "private.txt"}),
            (
                "d",
                "fs_download_open",
                {"environment_id": "alice-env", "path": "private.txt"},
            ),
            (
                "u",
                "fs_upload_open",
                {"environment_id": "alice-env", "path": "evil.bin", "size": 1},
            ),
        ):
            error = _error(bob, call_id, method, params)
            assert error["code"] == -32014, (method, error)
            assert error["data"]["kind"] == "authorization_denied"
