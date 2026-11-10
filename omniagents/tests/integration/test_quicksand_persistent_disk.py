"""Real VM regression: no model calls, no production state."""

import asyncio
import json
import os
import time

import pytest
from pathlib import Path
from omniagents.core.sandbox.quicksand import QuicksandClient, QuicksandOptions
from omniagents.core.sandbox.lifecycle_session import resolve_backend_session
from omniagents.integrations import quicksand_maintenance as maintenance

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("OMNI_TEST_QUICKSAND") != "1", reason="opt-in real VM proof"
    ),
]


async def test_persistent_disk_restarts_and_backup_restore(tmp_path):
    root = tmp_path
    client = QuicksandClient(state_dir=root / "vms")
    session = await client.create(
        options=QuicksandOptions(memory="1G", cpus=2, save_interval_seconds=1)
    )
    inner = resolve_backend_session(session)
    await inner.start()
    await inner.persist_durable()
    print("BOOTED", flush=True)
    assert (
        await inner.exec(
            "mkdir -p /workspace; echo persisted > /workspace/proof; echo installed > /usr/local/bin/persistence-proof"
        )
    ).ok()
    disk = Path(inner.state.directory) / "working/disk.qcow2"
    inode = disk.stat().st_ino
    await asyncio.sleep(3)
    assert not (disk.parent.parent / "CURRENT").exists()
    record = {
        "backend": "quicksand",
        "container_id": inner.state.container_id,
        "session_state": inner.state.model_dump(mode="json"),
    }
    backup = await asyncio.to_thread(maintenance.backup, record, root / "backups")
    print("ONLINE BACKUP", backup, flush=True)
    assert disk.stat().st_ino == inode
    checkpoint = (disk.parent.parent / "CURRENT").read_text()
    for i in range(3):
        state = inner.state
        start = time.monotonic()
        await inner.suspend_durable()
        print("STOP", i, round(time.monotonic() - start, 2), flush=True)
        assert disk.exists() and disk.stat().st_ino == inode
        inner = resolve_backend_session(await client.resume(state))
        await inner.start()
        await inner.persist_durable()
        result = await inner.exec(
            "cat /workspace/proof /usr/local/bin/persistence-proof"
        )
        assert result.ok() and result.stdout == b"persisted\ninstalled\n", result
        assert (disk.parent.parent / "CURRENT").read_text() == checkpoint
        print("RESTART", i, "verified", flush=True)
    await inner.suspend_durable()
    await asyncio.to_thread(
        maintenance.restore,
        root / "restored.json",
        backup,
        vm_root=root / "restored-vms",
    )
    data = json.loads((root / "restored.json").read_text())
    restored_client = QuicksandClient(state_dir=root / "restored-vms")
    restored = resolve_backend_session(
        await restored_client.resume(
            restored_client.deserialize_session_state(data["session_state"])
        )
    )
    await restored.start()
    result = await restored.exec(
        "cat /workspace/proof /usr/local/bin/persistence-proof"
    )
    assert result.stdout == b"persisted\ninstalled\n", result
    await restored.suspend_durable()
    print("RESTORE VERIFIED", flush=True)
