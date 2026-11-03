"""Persistence boundaries and capability declarations, without booting a VM."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.sandbox.manifest import Manifest
from omniagents.core.sandbox.quicksand import (
    QuicksandClient,
    QuicksandOptions,
    QuicksandSession,
)
from omniagents.core.runtime.environment_provisioner import sandbox_capabilities
from omniagents.execution import EnvironmentCapability


@pytest.mark.asyncio
async def test_shutdown_flushes_without_backup_and_keeps_disk(tmp_path):
    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path).create()).state
    )
    working = Path(session.state.directory) / "working"
    working.mkdir(parents=True)
    disk = working / "disk.qcow2"
    disk.write_bytes(b"installed packages and workspace")
    session.vm = SimpleNamespace(is_running=True, stop=AsyncMock())
    session._flush_disk = AsyncMock()
    session.persist_durable = AsyncMock(
        side_effect=AssertionError("shutdown must not back up")
    )
    await session.suspend_durable()
    session._flush_disk.assert_awaited_once()
    assert disk.read_bytes() == b"installed packages and workspace"
    assert session.vm is None


@pytest.mark.asyncio
async def test_failed_flush_prevents_destructive_stop(tmp_path):
    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path).create()).state
    )
    session.vm = SimpleNamespace(is_running=True, stop=AsyncMock())
    session._flush_disk = AsyncMock(side_effect=OSError("flush failed"))
    with pytest.raises(OSError, match="flush failed"):
        await session.suspend_durable()
    session.vm.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_uses_working_state_without_snapshot(tmp_path):
    client = QuicksandClient(state_dir=tmp_path)
    wrapped = await client.create(manifest=Manifest(root="/workspace"))
    stale = client.serialize_session_state(wrapped.state)
    session = QuicksandSession(wrapped.state)
    session.state.workspace_root_ready = True
    session._write_working_state()
    resumed = await client.resume(client.deserialize_session_state(stale))
    assert resumed.state.workspace_root_ready
    assert not (Path(session.state.directory) / "CURRENT").exists()


@pytest.mark.asyncio
async def test_old_interval_does_not_schedule_snapshots(tmp_path):
    import asyncio

    session = QuicksandSession(
        (
            await QuicksandClient(state_dir=tmp_path).create(
                options=QuicksandOptions(save_interval_seconds=0.01)
            )
        ).state
    )
    session.persist_durable = AsyncMock()
    await session._after_start()
    await asyncio.sleep(0.05)
    await session._cancel_saver()
    session.persist_durable.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_rejects_foreign_directory_and_identity(tmp_path):
    client = QuicksandClient(state_dir=tmp_path)
    session = await client.create()
    with pytest.raises(ValueError, match="directory"):
        await QuicksandClient(state_dir=tmp_path / "different").resume(session.state)
    directory = Path(session.state.directory)
    generation = directory / "save-test"
    generation.mkdir(parents=True)
    (directory / "CURRENT").write_text("save-test")
    payload = client.serialize_session_state(session.state)
    payload["container_id"] = "different"
    (generation / "omni-state.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="identity"):
        await client.resume(session.state)


@pytest.mark.asyncio
async def test_exposed_ports_require_explicit_loopback_mapping(tmp_path):
    client = QuicksandClient(state_dir=tmp_path)
    with pytest.raises(ValueError, match="port_forwards"):
        await client.create(options=QuicksandOptions(exposed_ports=(8000,)))
    session = await client.create(
        options=QuicksandOptions(exposed_ports=(8000,), port_forwards={8000: 18000})
    )
    endpoint = await session.resolve_exposed_port(8000)
    assert (endpoint.host, endpoint.port) == ("127.0.0.1", 18000)


@pytest.mark.asyncio
async def test_guest_desktop_requires_mapping_and_excludes_native_display(tmp_path):
    client = QuicksandClient(state_dir=tmp_path)
    with pytest.raises(ValueError, match="port_forwards"):
        await client.create(options=QuicksandOptions(desktop_vnc_port=5901))
    with pytest.raises(ValueError, match="either"):
        await client.create(
            options=QuicksandOptions(
                desktop_vnc_port=5901,
                enable_display=True,
                port_forwards={5901: 15901},
            )
        )
    session = await client.create(
        options=QuicksandOptions(
            desktop_vnc_port=5901,
            port_forwards={5901: 15901},
        )
    )
    assert session.state.options.desktop_vnc_port == 5901


@pytest.mark.parametrize(
    "options,network",
    [
        ({}, False),
        ({"network_mode": "full"}, True),
        ({"host_services": {7300: 7300}}, True),
    ],
)
def test_network_capabilities_count_host_services_as_network(options, network):
    caps = sandbox_capabilities(
        {"client": {"type": "quicksand"}, "options": options}, SimpleNamespace()
    )
    assert EnvironmentCapability.CONTAINED in caps
    assert EnvironmentCapability.PTY in caps
    assert (EnvironmentCapability.NETWORK in caps) == network


@pytest.mark.asyncio
async def test_detach_saves_quicksand_and_propagates_save_failure():
    from omniagents.core.runtime.serve_harness import teardown_session

    inner = SimpleNamespace(
        suspend_durable=AsyncMock(side_effect=OSError("cannot save"))
    )
    with pytest.raises(OSError, match="cannot save"):
        await teardown_session(SimpleNamespace(_inner=inner), [], detach=True)
    inner.suspend_durable.assert_awaited_once()


@pytest.mark.asyncio
async def test_subsequent_save_includes_frozen_parent_layers(tmp_path):
    client = QuicksandClient(state_dir=tmp_path)
    session = QuicksandSession((await client.create()).state)
    parent = tmp_path / "parent"
    (parent / "overlays").mkdir(parents=True)
    (parent / "overlays/0.qcow2").write_bytes(b"installed packages")
    (parent / "overlays/1.qcow2").write_bytes(b"previous work")
    generation = tmp_path / "next"
    (generation / "overlays").mkdir(parents=True)
    (generation / "manifest.json").write_text('{"version":6}')
    (generation / "overlays/0.qcow2").write_bytes(b"new work")
    session._loaded_generation = parent
    from omniagents.core.sandbox.quicksand import include_parent_overlays

    include_parent_overlays(parent, generation)
    assert [
        (generation / f"overlays/{n:08d}.qcow2").read_bytes() for n in range(3)
    ] == [
        b"installed packages",
        b"previous work",
        b"new work",
    ]
    assert (parent / "overlays/0.qcow2").read_bytes() == b"installed packages"


@pytest.mark.asyncio
async def test_unclean_marker_blocks_automatic_resume(tmp_path, monkeypatch):
    import sys

    client = QuicksandClient(state_dir=tmp_path)
    session = QuicksandSession((await client.create()).state, resumed=True)
    directory = Path(session.state.directory)
    directory.mkdir()
    (directory / "RUNNING").write_text("previous process")
    constructor = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "quicksand",
        SimpleNamespace(
            Sandbox=constructor,
            Forward=None,
            Host=None,
            Guest=None,
            NetworkMode=None,
            Mount=None,
        ),
    )
    with pytest.raises(RuntimeError, match="uncleanly"):
        await session.start()
    constructor.assert_not_called()
    assert (directory / "RUNNING").exists()
    assert not session._lock.is_locked


@pytest.mark.asyncio
async def test_reset_requires_backup_and_stopped_vm(tmp_path, monkeypatch):
    from omniagents.integrations import quicksand_maintenance as maintenance
    from omniagents.integrations.personal_workspace import write_state
    from filelock import FileLock, Timeout

    wrapped = await QuicksandClient(state_dir=tmp_path / "vms").create()
    directory = Path(wrapped.state.directory)
    directory.mkdir(parents=True)
    state_file = tmp_path / "factory.json"
    write_state(
        state_file,
        {
            "backend": "quicksand",
            "container_id": wrapped.state.container_id,
            "session_state": wrapped.state.model_dump(mode="json"),
        },
    )
    backup_dir = tmp_path / "backups"
    with FileLock(str(directory / "owner.lock")):
        with pytest.raises(Timeout):
            maintenance.reset(state_file, backup_dir)
    (directory / "RUNNING").touch()
    with pytest.raises(RuntimeError, match="cleanly"):
        maintenance.reset(state_file, backup_dir)
    (directory / "RUNNING").unlink()
    with pytest.raises(ValueError, match="outside"):
        maintenance.reset(state_file, directory / "backups")

    def failed_backup(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(maintenance, "backup", failed_backup)
    with pytest.raises(OSError, match="disk full"):
        maintenance.reset(state_file, backup_dir)
    assert state_file.exists() and directory.exists()


@pytest.mark.asyncio
async def test_backup_failure_keeps_previous_backup(tmp_path):
    from omniagents.integrations import quicksand_maintenance as maintenance

    wrapped = await QuicksandClient(state_dir=tmp_path / "vms").create()
    Path(wrapped.state.directory).mkdir(parents=True)
    backups = tmp_path / "backups"
    previous = backups / "personal-previous"
    previous.mkdir(parents=True)
    record = {
        "backend": "quicksand",
        "container_id": wrapped.state.container_id,
        "session_state": wrapped.state.model_dump(mode="json"),
    }
    with pytest.raises(FileNotFoundError):
        maintenance.backup(record, backups, keep=1)
    assert list(backups.iterdir()) == [previous]
    # Retention must never prune the new backup, even when an older name
    # sorts after it (for example, random suffixes within the same second).
    directory = Path(wrapped.state.directory)
    generation = directory / "save-test"
    generation.mkdir()
    (generation / "omni-state.json").write_text(wrapped.state.model_dump_json())
    (directory / "CURRENT").write_text("save-test")
    saved = maintenance.backup(record, backups, keep=1)
    assert saved.exists() and not previous.exists()


def test_explicit_vm_profile_ignores_docker_user_layer(tmp_path, monkeypatch):
    from omniagents.core.sandbox.profiles import (
        product_default_profile_path,
        resolve_profile,
    )

    selected = tmp_path / "vm.yml"
    selected.write_text("client: {type: quicksand}\nmanifest: {root: /workspace}\n")
    config = tmp_path / "config"
    (config / "sandbox").mkdir(parents=True)
    (config / "sandbox/default.yml").write_text(
        "client: {type: docker, network_mode: host}\n"
    )
    monkeypatch.setenv("OMNIAGENTS_SANDBOX_PROFILE", str(selected))
    assert product_default_profile_path() == selected
    docker = tmp_path / "docker.yml"
    docker.write_text(
        "client: {type: docker}\noptions: {image: node:22}\nbuild: [docker-only-step]\n"
    )
    profile = resolve_profile(
        workspace_dir=tmp_path,
        config_dir=config,
        default_profile_path=docker,
    )
    assert not profile.build
    assert "options" not in profile.raw
    assert profile.raw["client"] == {"type": "quicksand"}


def test_container_disk_io_override_is_scoped_to_drive_arguments(monkeypatch):
    import sys
    from omniagents.core.sandbox.quicksand_runtime import sandbox_class

    class FakeSandbox:
        def _build_vm_command(self):
            return ["qemu", "-drive", "file=disk,aio=io_uring", "-name", "aio=io_uring"]

    monkeypatch.setitem(sys.modules, "quicksand", SimpleNamespace(Sandbox=FakeSandbox))
    monkeypatch.delenv("OMNIAGENTS_QUICKSAND_DISK_AIO", raising=False)
    vm = sandbox_class()()
    assert vm._build_vm_command()[2] == "file=disk,aio=io_uring"
    monkeypatch.setenv("OMNIAGENTS_QUICKSAND_DISK_AIO", "threads")
    assert vm._build_vm_command() == [
        "qemu",
        "-drive",
        "file=disk,aio=threads",
        "-name",
        "aio=io_uring",
    ]


def test_saved_overlay_order_survives_more_than_ten_checkpoints(tmp_path):
    from omniagents.core.sandbox.quicksand import normalize_saved_overlay_order

    overlays = tmp_path / "overlays"
    overlays.mkdir()
    for index in range(14):
        (overlays / f"{index}.qcow2").write_text(str(index))
    normalize_saved_overlay_order(tmp_path)
    # Match the SDK loader's lexical sorting, which previously loaded stale
    # layers above the newest changes once the chain reached double digits.
    assert [int(p.read_text()) for p in sorted(overlays.glob("*.qcow2"))] == list(
        range(14)
    )
    normalize_saved_overlay_order(tmp_path)
    assert len(list(overlays.glob("*.qcow2"))) == 14


@pytest.mark.asyncio
async def test_cancelled_snapshot_finishes_before_lock_released(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from omniagents.core.sandbox.quicksand import QuicksandSession

    # A blocked disk write models the SDK worker continuing after caller cancellation.
    session = object.__new__(QuicksandSession)
    session.vm = SimpleNamespace(is_running=True)
    session.state = SimpleNamespace(directory=str(tmp_path))
    session._save_lock = asyncio.Lock()
    entered = asyncio.Event()
    finish = asyncio.Event()
    completed = []

    async def save():
        entered.set()
        await finish.wait()
        completed.append(True)

    session._save_generation = save
    task = asyncio.create_task(session.backup_durable())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert session._save_lock.locked() and not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed == [True] and not session._save_lock.locked()


def test_compaction_preserves_zeroes_and_writes(tmp_path):
    """Run when QEMU is available; a zero over a nonzero base must stay zero."""
    import os
    import shutil
    import subprocess
    from omniagents.core.sandbox.quicksand import compact_saved_overlay_chain

    qemu = os.environ.get("OMNI_TEST_QEMU_IMG") or shutil.which("qemu-img")
    if not qemu:
        pytest.skip("Set OMNI_TEST_QEMU_IMG to run real QEMU consolidation")

    def run(*args):
        subprocess.run([qemu, *map(str, args)], check=True, capture_output=True)

    raw = tmp_path / "raw"
    raw.write_bytes(b"A" * 1024 * 1024 + b"\0" * 7 * 1024 * 1024)
    base = tmp_path / "base.qcow2"
    run("convert", "-f", "raw", "-O", "qcow2", raw, base)
    generation = tmp_path / "save"
    overlays = generation / "overlays"
    overlays.mkdir(parents=True)
    previous = base
    for i in range(3):
        with raw.open("r+b") as stream:
            stream.seek(i * 1024 * 1024)
            stream.write((b"\0" if i == 0 else bytes([65 + i])) * 1024 * 1024)
        layer = overlays / f"{i:08d}.qcow2"
        run(
            "convert",
            "-f",
            "raw",
            "-O",
            "qcow2",
            "-B",
            previous,
            "-F",
            "qcow2",
            raw,
            layer,
        )
        previous = layer
    run(
        "create",
        "-f",
        "qcow2",
        "-b",
        previous,
        "-F",
        "qcow2",
        overlays / "00000003.qcow2",
    )
    compact_saved_overlay_chain(generation, qemu, max_layers=1)
    assert len(list(overlays.glob("*.qcow2"))) == 1
    run("compare", "-f", "raw", "-F", "qcow2", raw, overlays / "00000000.qcow2")


@pytest.mark.asyncio
async def test_checkpoint_rejection_retries_without_repeating_executed_command(
    tmp_path,
):
    client = QuicksandClient(state_dir=tmp_path)
    wrapped = await client.create(manifest=Manifest(root="/workspace"))
    session = QuicksandSession(wrapped.state)
    busy = SimpleNamespace(
        exit_code=-1, stdout="", stderr="Exclusive command in progress"
    )
    ok = SimpleNamespace(exit_code=0, stdout="ZXhpc3Rpbmc=", stderr="")
    session.vm = SimpleNamespace(execute=AsyncMock(side_effect=[busy, ok]))
    assert (await session.read(Path("/workspace/existing"))).read() == b"existing"
    assert session.vm.execute.await_count == 2
    failed = SimpleNamespace(exit_code=1, stdout="already executed", stderr="failure")
    session.vm.execute = AsyncMock(return_value=failed)
    result = await session.exec("side-effecting-command")
    assert result.exit_code == 1
    assert session.vm.execute.await_count == 1


@pytest.mark.asyncio
async def test_checkpoint_wait_is_bounded_and_read_errors_are_preserved(tmp_path):
    import asyncio

    client = QuicksandClient(state_dir=tmp_path)
    session = QuicksandSession(
        (await client.create(manifest=Manifest(root="/workspace"))).state
    )
    session.vm = SimpleNamespace(execute=AsyncMock())
    session._guest_ready.clear()
    with pytest.raises(TimeoutError):
        await session._execute_guest("test", timeout=0.01)
    session.vm.execute.assert_not_awaited()
    session._guest_ready.set()
    for code, message, error in [
        (44, "", FileNotFoundError),
        (45, "", PermissionError),
        (-1, "agent disconnected", OSError),
    ]:
        session.vm.execute = AsyncMock(
            return_value=SimpleNamespace(exit_code=code, stdout="", stderr=message)
        )
        with pytest.raises(error):
            await session.read(Path("/workspace/file"))
        assert session.vm.execute.await_count == 1
    session.vm.execute = AsyncMock(
        return_value=SimpleNamespace(
            exit_code=-1, stdout="", stderr="Exclusive command in progress"
        )
    )
    with pytest.raises(TimeoutError, match="checkpoint"):
        await session._execute_guest("test", timeout=0.02)


@pytest.mark.asyncio
async def test_explicit_backup_does_not_block_guest_commands(tmp_path):
    import asyncio

    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path).create()).state
    )
    Path(session.state.directory).mkdir()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def backup():
        entered.set()
        await finish.wait()

    session._save_generation = backup
    session.vm = SimpleNamespace(
        is_running=True,
        execute=AsyncMock(
            return_value=SimpleNamespace(exit_code=0, stdout="ok", stderr="")
        ),
    )
    task = asyncio.create_task(session.backup_durable())
    await entered.wait()
    result = await asyncio.wait_for(session.exec("echo ok"), 1)
    assert result.stdout == b"ok"
    finish.set()
    await task


@pytest.mark.asyncio
async def test_failed_explicit_backup_keeps_current(tmp_path, monkeypatch):
    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path).create()).state
    )
    directory = Path(session.state.directory)
    directory.mkdir()
    (directory / "CURRENT").write_text("save-before")

    async def execute(command, **kwargs):
        if command == "query-jobs":
            return {
                "return": [
                    {"id": execute.job, "status": "concluded", "error": "disk full"}
                ]
            }
        if command == "drive-backup":
            execute.job = kwargs["job-id"]
        return {"return": {}}

    session.vm = SimpleNamespace(
        is_running=True, _qmp_client=SimpleNamespace(execute=execute)
    )
    session._flush_disk = AsyncMock()
    with pytest.raises(RuntimeError, match="disk full"):
        await session.backup_durable()
    assert (directory / "CURRENT").read_text() == "save-before"


@pytest.mark.asyncio
async def test_stopped_backup_copies_working_disk_not_old_checkpoint(tmp_path):
    from omniagents.integrations.quicksand_maintenance import backup

    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path / "vms").create()).state
    )
    session._write_working_state()
    directory = Path(session.state.directory)
    (directory / "working/disk.qcow2").write_bytes(b"latest installed packages")
    (directory / "CURRENT").write_text("save-obsolete")
    record = {"session_state": session.state.model_dump(mode="json")}
    result = backup(record, tmp_path / "backups")
    assert (result / "vm/disk.qcow2").read_bytes() == b"latest installed packages"


@pytest.mark.asyncio
async def test_startup_persistence_flushes_without_creating_backup(tmp_path):
    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path).create()).state
    )
    session.vm = SimpleNamespace(is_running=True)
    session._flush_disk = AsyncMock()
    session.backup_durable = AsyncMock(
        side_effect=AssertionError("startup is not backup")
    )
    await session.persist_durable()
    session._flush_disk.assert_awaited_once()
    session.backup_durable.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_acknowledges_an_already_requested_backup(tmp_path):
    import asyncio

    session = QuicksandSession(
        (await QuicksandClient(state_dir=tmp_path).create()).state
    )
    entered, finish = asyncio.Event(), asyncio.Event()

    async def backup():
        entered.set()
        await finish.wait()

    session.backup_durable = backup
    await session._after_start()
    directory = Path(session.state.directory)
    (directory / "backup.request").write_text("test-request")
    await asyncio.wait_for(entered.wait(), 2)
    stopping = asyncio.create_task(session._cancel_saver())
    await asyncio.sleep(0)
    assert not stopping.done()
    finish.set()
    await stopping
    assert json.loads((directory / "backup.result").read_text()) == {
        "id": "test-request",
        "ok": True,
    }
    assert not (directory / "backup.request").exists()
