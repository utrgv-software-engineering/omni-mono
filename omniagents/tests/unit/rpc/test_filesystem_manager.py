"""Unit tests for the fs_* FilesystemManager (tkt_B5fXOZHf08K1).

Covers: canonical-path confinement (adversarial), watch lifecycle with
coalesced diffs, overflow → fs_rescan_required, the generic (substrate-
neutral) scan mechanism, symlink-escape rejection, chunked download/upload
round-trips with checksum verification, sequential-offset enforcement,
cancellation, and per-connection cleanup.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from typing import Any, Dict, List, Optional

import pytest

from omniagents.rpc.filesystem import (
    FilesystemManager,
    FsNotFoundError,
    FsPathError,
    FsTransferError,
    FsWatchError,
    canonical_relpath,
)
from omniagents.workspace.host import HostWorkspace
from omniagents.workspace.types import FileEntry

TIMEOUT = 5.0


class FakeChannel:
    """Collects notifications like a JsonRpcChannel would."""

    def __init__(self) -> None:
        self.notifications: List[tuple[str, Dict[str, Any]]] = []
        self.fail = False

    async def async_call(self, method: str, args: Optional[Dict[str, Any]] = None):
        if self.fail:
            raise RuntimeError("socket closed")
        self.notifications.append((method, args or {}))

    def by_method(self, method: str) -> List[Dict[str, Any]]:
        return [payload for name, payload in self.notifications if name == method]


class GenericWorkspace:
    """A non-host substrate double that delegates to a HostWorkspace.

    Deliberately NOT a HostWorkspace subclass, so the manager takes the
    generic ``poll:workspace`` / staged-transfer paths that sandbox and
    remote workspaces use, while the underlying I/O still hits tmp_path.
    """

    def __init__(self, root: str) -> None:
        self._inner = HostWorkspace(root=root)
        self.root = self._inner.root

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def make_manager(workspace, **overrides) -> FilesystemManager:
    async def resolver(session_id: str):
        return workspace

    defaults = dict(
        workspace_resolver=resolver,
        min_poll_interval=0.01,
        poll_interval_host=0.02,
        poll_interval_generic=0.02,
        progress_interval=0.0,
    )
    defaults.update(overrides)
    return FilesystemManager(**defaults)


async def wait_for(predicate, timeout: float = TIMEOUT) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


# ---------------------------------------------------------------------------
#  Path canonicalization (adversarial)
# ---------------------------------------------------------------------------


def test_canonical_relpath_normalizes():
    assert canonical_relpath("") == "."
    assert canonical_relpath(".") == "."
    assert canonical_relpath("a/b") == "a/b"
    assert canonical_relpath("./a//b/") == "a/b"
    assert canonical_relpath("a/./b") == "a/b"
    assert canonical_relpath("a/x/../b") == "a/b"


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "../etc/passwd",
        "a/../../etc/passwd",
        "/etc/passwd",
        "//etc/passwd",
        "C:/windows",
        "c:\\windows",
        "a\\b",
        "a/\x00b",
    ],
)
def test_canonical_relpath_rejects_escapes(path):
    with pytest.raises(FsPathError):
        canonical_relpath(path)


def test_canonical_relpath_rejects_non_string():
    with pytest.raises(FsPathError):
        canonical_relpath(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
#  Listings and stat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_and_stat(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "a.txt").write_text("hello")
    manager = make_manager(HostWorkspace(root=str(tmp_path)))

    listing = await manager.list("s1", ".", recursive=True)
    paths = {entry["path"]: entry for entry in listing["entries"]}
    assert paths["dir"]["type"] == "directory"
    assert paths["dir/a.txt"]["type"] == "file"
    assert paths["dir/a.txt"]["size"] == 5
    assert listing["truncated"] is False

    info = await manager.stat("s1", "dir/a.txt")
    assert info["type"] == "file"
    assert info["size"] == 5
    assert info["mime"] == "text/plain"

    with pytest.raises(FsNotFoundError):
        await manager.stat("s1", "missing.txt")
    with pytest.raises(FsPathError):
        await manager.stat("s1", "../outside")
    with pytest.raises(FsNotFoundError):
        await manager.list("s1", "dir/a.txt")


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    root = tmp_path / "workspace"
    root.mkdir()
    os.symlink(outside / "secret.txt", root / "link.txt")
    manager = make_manager(HostWorkspace(root=str(root)))

    with pytest.raises(FsPathError):
        await manager.stat("s1", "link.txt")
    with pytest.raises(FsPathError):
        await manager.download_open("s1", "link.txt", channel=FakeChannel())


@pytest.mark.asyncio
async def test_scan_does_not_descend_symlinked_directories(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    root = tmp_path / "workspace"
    root.mkdir()
    os.symlink(outside, root / "linkdir")
    manager = make_manager(HostWorkspace(root=str(root)))

    listing = await manager.list("s1", ".", recursive=True)
    paths = {entry["path"] for entry in listing["entries"]}
    assert "linkdir" in paths
    assert not any("secret.txt" in path for path in paths)


# ---------------------------------------------------------------------------
#  Watch lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_detects_create_modify_delete(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    result = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=20, channel=channel
    )
    assert result["mechanism"] == "poll:host"
    watch_id = result["watch_id"]

    try:
        target = tmp_path / "new.txt"
        target.write_text("one")
        await wait_for(
            lambda: any(
                event["type"] == "created" and event["path"] == "new.txt"
                for payload in channel.by_method("fs_events")
                for event in payload["events"]
            )
        )
        payload = channel.by_method("fs_events")[0]
        assert payload["watch_id"] == watch_id
        assert payload["session_id"] == "s1"

        target.write_text("two-longer")
        await wait_for(
            lambda: any(
                event["type"] == "modified" and event["path"] == "new.txt"
                for payload in channel.by_method("fs_events")
                for event in payload["events"]
            )
        )

        target.unlink()
        await wait_for(
            lambda: any(
                event["type"] == "deleted" and event["path"] == "new.txt"
                for payload in channel.by_method("fs_events")
                for event in payload["events"]
            )
        )
    finally:
        assert await manager.unwatch("s1", watch_id, channel=channel) is True
    assert manager.watch_count() == 0


@pytest.mark.asyncio
async def test_watch_coalesces_one_event_per_path_per_cycle(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    result = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=200, channel=channel
    )
    try:
        # Multiple writes inside one (long) poll cycle …
        target = tmp_path / "burst.txt"
        target.write_text("a")
        target.write_text("bb")
        target.write_text("ccc")
        await wait_for(lambda: bool(channel.by_method("fs_events")))
        events = [
            event
            for payload in channel.by_method("fs_events")
            for event in payload["events"]
            if event["path"] == "burst.txt"
        ]
        # … coalesce into exactly one event for the path.
        assert len(events) == 1
        assert events[0]["type"] == "created"
    finally:
        await manager.unwatch("s1", result["watch_id"], channel=channel)


@pytest.mark.asyncio
async def test_watch_overflow_emits_rescan_required_and_keeps_running(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)), max_events_per_cycle=3)
    channel = FakeChannel()
    result = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=20, channel=channel
    )
    try:
        for index in range(10):
            (tmp_path / f"file-{index}.txt").write_text("x")
        await wait_for(lambda: bool(channel.by_method("fs_rescan_required")))
        payload = channel.by_method("fs_rescan_required")[0]
        assert payload["reason"] == "event_overflow"
        assert payload["watch_id"] == result["watch_id"]
        # No event flood was delivered for the burst.
        flood = [
            event
            for frame in channel.by_method("fs_events")
            for event in frame["events"]
            if event["path"].startswith("file-")
        ]
        assert not flood
        # The watch keeps running against the fresh snapshot.
        (tmp_path / "after.txt").write_text("y")
        await wait_for(
            lambda: any(
                event["path"] == "after.txt"
                for frame in channel.by_method("fs_events")
                for event in frame["events"]
            )
        )
    finally:
        await manager.unwatch("s1", result["watch_id"], channel=channel)


@pytest.mark.asyncio
async def test_watch_scan_limit_terminates_watch(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)), max_watch_entries=5)
    channel = FakeChannel()
    result = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=20, channel=channel
    )
    for index in range(10):
        (tmp_path / f"grow-{index}.txt").write_text("x")
    await wait_for(lambda: bool(channel.by_method("fs_rescan_required")))
    assert channel.by_method("fs_rescan_required")[0]["reason"] == "scan_limit_exceeded"
    await wait_for(lambda: manager.watch_count() == 0)
    with pytest.raises(FsWatchError):
        await manager.unwatch("s1", result["watch_id"], channel=channel)


@pytest.mark.asyncio
async def test_watch_rejects_oversized_tree_at_subscribe(tmp_path):
    for index in range(10):
        (tmp_path / f"pre-{index}.txt").write_text("x")
    manager = make_manager(HostWorkspace(root=str(tmp_path)), max_watch_entries=5)
    with pytest.raises(FsWatchError):
        await manager.watch("s1", ".", recursive=True, channel=FakeChannel())


@pytest.mark.asyncio
async def test_generic_workspace_mechanism_and_events(tmp_path):
    manager = make_manager(GenericWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    result = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=20, channel=channel
    )
    assert result["mechanism"] == "poll:workspace"
    try:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.txt").write_text("deep")
        await wait_for(
            lambda: {
                event["path"]
                for frame in channel.by_method("fs_events")
                for event in frame["events"]
            }
            >= {"sub", "sub/deep.txt"}
        )
    finally:
        await manager.unwatch("s1", result["watch_id"], channel=channel)


@pytest.mark.asyncio
async def test_unwatch_requires_owning_channel_and_session(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    result = await manager.watch("s1", ".", channel=channel)
    with pytest.raises(FsWatchError):
        await manager.unwatch("s1", result["watch_id"], channel=FakeChannel())
    with pytest.raises(FsWatchError):
        await manager.unwatch("other", result["watch_id"], channel=channel)
    assert await manager.unwatch("s1", result["watch_id"], channel=channel)


@pytest.mark.asyncio
async def test_watch_limit_per_connection(tmp_path):
    manager = make_manager(
        HostWorkspace(root=str(tmp_path)), max_watches_per_connection=2
    )
    channel = FakeChannel()
    await manager.watch("s1", ".", channel=channel)
    await manager.watch("s1", ".", channel=channel)
    with pytest.raises(FsWatchError):
        await manager.watch("s1", ".", channel=channel)
    manager.cleanup_channel(channel)
    assert manager.watch_count() == 0


@pytest.mark.asyncio
async def test_cleanup_channel_stops_watches_and_transfers(tmp_path):
    (tmp_path / "file.bin").write_bytes(b"payload")
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    await manager.watch("s1", ".", channel=channel)
    await manager.download_open("s1", "file.bin", channel=channel)
    await manager.upload_open("s1", "up.bin", 3, channel=channel)
    assert manager.watch_count() == 1
    assert manager.transfer_count() == 2
    manager.cleanup_channel(channel)
    assert manager.watch_count() == 0
    assert manager.transfer_count() == 0


# ---------------------------------------------------------------------------
#  Downloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_cls", [HostWorkspace, GenericWorkspace])
async def test_download_round_trip_with_checksum(tmp_path, workspace_cls):
    payload = os.urandom(1000)
    (tmp_path / "blob.bin").write_bytes(payload)
    manager = make_manager(workspace_cls(root=str(tmp_path)), chunk_size=256)
    channel = FakeChannel()

    opened = await manager.download_open("s1", "blob.bin", channel=channel)
    assert opened["size"] == 1000
    assert opened["sha256"] == hashlib.sha256(payload).hexdigest()
    assert opened["mime"] == "application/octet-stream"
    assert opened["chunk_size"] == 256

    received = b""
    while True:
        chunk = await manager.download_read(
            "s1", opened["transfer_id"], channel=channel
        )
        received += base64.b64decode(chunk["data"])
        if chunk["eof"]:
            break
    assert received == payload
    assert hashlib.sha256(received).hexdigest() == opened["sha256"]
    # Progress was reported up to the total.
    progress = channel.by_method("fs_transfer_progress")
    assert progress
    assert progress[-1]["transferred"] == 1000
    assert progress[-1]["total"] == 1000
    assert progress[-1]["direction"] == "download"
    assert await manager.download_close("s1", opened["transfer_id"], channel=channel)
    with pytest.raises(FsTransferError):
        await manager.download_read("s1", opened["transfer_id"], channel=channel)


@pytest.mark.asyncio
async def test_download_resume_from_offset(tmp_path):
    payload = bytes(range(256)) * 4
    (tmp_path / "blob.bin").write_bytes(payload)
    manager = make_manager(HostWorkspace(root=str(tmp_path)), chunk_size=128)
    channel = FakeChannel()
    opened = await manager.download_open("s1", "blob.bin", channel=channel)
    chunk = await manager.download_read(
        "s1", opened["transfer_id"], offset=512, length=100, channel=channel
    )
    assert base64.b64decode(chunk["data"]) == payload[512:612]
    assert chunk["offset"] == 512
    with pytest.raises(FsTransferError):
        await manager.download_read(
            "s1", opened["transfer_id"], offset=-1, channel=channel
        )
    with pytest.raises(FsTransferError):
        await manager.download_read(
            "s1", opened["transfer_id"], length=10_000_000, channel=channel
        )


@pytest.mark.asyncio
async def test_download_denies_wrong_channel_or_session(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"data")
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    opened = await manager.download_open("s1", "blob.bin", channel=channel)
    with pytest.raises(FsTransferError):
        await manager.download_read("s1", opened["transfer_id"], channel=FakeChannel())
    with pytest.raises(FsTransferError):
        await manager.download_read("s2", opened["transfer_id"], channel=channel)


@pytest.mark.asyncio
async def test_download_open_missing_and_directory(tmp_path):
    (tmp_path / "dir").mkdir()
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    with pytest.raises(FsNotFoundError):
        await manager.download_open("s1", "nope.bin", channel=FakeChannel())
    with pytest.raises(FsNotFoundError):
        await manager.download_open("s1", "dir", channel=FakeChannel())
    with pytest.raises(FsPathError):
        await manager.download_open("s1", ".", channel=FakeChannel())


@pytest.mark.asyncio
async def test_download_read_never_follows_a_symlink_swapped_in_mid_transfer(
    tmp_path,
):
    """Confinement must not depend on the path still meaning what it did.

    The path is resolved once, at open; chunks come from the handle that
    open retained. Replacing the file with a symlink pointing outside the
    workspace root afterwards must not make a single outside byte
    readable through the transfer.
    """
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"SECRET-OUTSIDE-BYTES")
    payload = b"A" * 4096
    target = root / "swap.bin"
    target.write_bytes(payload)

    manager = make_manager(HostWorkspace(root=str(root)), chunk_size=1024)
    channel = FakeChannel()
    opened = await manager.download_open("s1", "swap.bin", channel=channel)
    assert opened["size"] == len(payload)

    # The path now points outside the workspace root.
    target.unlink()
    os.symlink(outside / "secret.txt", target)
    # Opening a *new* transfer through that path is refused outright.
    with pytest.raises(FsPathError):
        await manager.download_open("s1", "swap.bin", channel=channel)

    # And the transfer already in flight keeps serving the opened file.
    received = b""
    while True:
        chunk = await manager.download_read(
            "s1", opened["transfer_id"], channel=channel
        )
        received += base64.b64decode(chunk["data"])
        if chunk["eof"]:
            break
    assert received == payload
    assert b"SECRET" not in received
    assert hashlib.sha256(received).hexdigest() == opened["sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_cls", [HostWorkspace, GenericWorkspace])
async def test_download_open_rejects_payload_above_the_cap(tmp_path, workspace_cls):
    (tmp_path / "big.bin").write_bytes(b"z" * 4096)
    (tmp_path / "small.bin").write_bytes(b"z" * 16)
    manager = make_manager(workspace_cls(root=str(tmp_path)), max_download_bytes=1024)
    channel = FakeChannel()
    with pytest.raises(FsTransferError) as excinfo:
        await manager.download_open("s1", "big.bin", channel=channel)
    assert excinfo.value.data["reason"] == "size_out_of_range"
    # Nothing was registered or left open by the rejected transfer.
    assert manager.transfer_count() == 0
    # A file within the cap still opens.
    opened = await manager.download_open("s1", "small.bin", channel=channel)
    assert opened["size"] == 16


@pytest.mark.asyncio
async def test_oversized_download_is_refused_before_the_substrate_read(tmp_path):
    """The cap exists to protect memory: the read must not happen at all."""
    (tmp_path / "big.bin").write_bytes(b"z" * 4096)

    class CountingWorkspace(GenericWorkspace):
        def __init__(self, root: str) -> None:
            super().__init__(root)
            self.reads: List[str] = []

        async def read_bytes(self, path: str) -> bytes:
            self.reads.append(path)
            return await self._inner.read_bytes(path)

    workspace = CountingWorkspace(root=str(tmp_path))
    manager = make_manager(workspace, max_download_bytes=1024)
    with pytest.raises(FsTransferError):
        await manager.download_open("s1", "big.bin", channel=FakeChannel())
    assert workspace.reads == [], "oversized file was pulled into memory anyway"


# ---------------------------------------------------------------------------
#  Uploads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_cls", [HostWorkspace, GenericWorkspace])
async def test_upload_round_trip_with_checksum(tmp_path, workspace_cls):
    payload = os.urandom(700)
    digest = hashlib.sha256(payload).hexdigest()
    manager = make_manager(workspace_cls(root=str(tmp_path)), chunk_size=256)
    channel = FakeChannel()

    opened = await manager.upload_open(
        "s1", "nested/dir/out.bin", len(payload), sha256=digest, channel=channel
    )
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + 256]
        result = await manager.upload_chunk(
            "s1",
            opened["transfer_id"],
            offset,
            base64.b64encode(chunk).decode(),
            channel=channel,
        )
        offset += len(chunk)
        assert result["received"] == offset
    committed = await manager.upload_commit(
        "s1", opened["transfer_id"], channel=channel
    )
    assert committed == {
        "path": "nested/dir/out.bin",
        "size": len(payload),
        "sha256": digest,
    }
    assert (tmp_path / "nested" / "dir" / "out.bin").read_bytes() == payload
    progress = channel.by_method("fs_transfer_progress")
    assert progress and progress[-1]["direction"] == "upload"
    assert progress[-1]["transferred"] == len(payload)


@pytest.mark.asyncio
async def test_upload_enforces_sequential_offsets(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    opened = await manager.upload_open("s1", "out.bin", 10, channel=channel)
    encoded = base64.b64encode(b"12345").decode()
    await manager.upload_chunk("s1", opened["transfer_id"], 0, encoded, channel=channel)
    with pytest.raises(FsTransferError) as excinfo:
        await manager.upload_chunk(
            "s1", opened["transfer_id"], 3, encoded, channel=channel
        )
    assert excinfo.value.data["reason"] == "offset_mismatch"
    with pytest.raises(FsTransferError):
        await manager.upload_chunk(
            "s1", opened["transfer_id"], 5, "not-base64!!", channel=channel
        )
    # Declared size can't be exceeded.
    with pytest.raises(FsTransferError) as excinfo:
        await manager.upload_chunk(
            "s1",
            opened["transfer_id"],
            5,
            base64.b64encode(b"6789012345").decode(),
            channel=channel,
        )
    assert excinfo.value.data["reason"] == "size_exceeded"


@pytest.mark.asyncio
async def test_upload_commit_verifies_size_and_checksum(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    opened = await manager.upload_open("s1", "short.bin", 10, channel=channel)
    await manager.upload_chunk(
        "s1",
        opened["transfer_id"],
        0,
        base64.b64encode(b"12345").decode(),
        channel=channel,
    )
    with pytest.raises(FsTransferError) as excinfo:
        await manager.upload_commit("s1", opened["transfer_id"], channel=channel)
    assert excinfo.value.data["reason"] == "size_mismatch"

    wrong = await manager.upload_open(
        "s1", "wrong.bin", 5, sha256="0" * 64, channel=channel
    )
    await manager.upload_chunk(
        "s1",
        wrong["transfer_id"],
        0,
        base64.b64encode(b"12345").decode(),
        channel=channel,
    )
    with pytest.raises(FsTransferError) as excinfo:
        await manager.upload_commit("s1", wrong["transfer_id"], channel=channel)
    assert excinfo.value.data["reason"] == "checksum_mismatch"
    assert not (tmp_path / "wrong.bin").exists()


@pytest.mark.asyncio
async def test_upload_abort_cancels_mid_transfer(tmp_path):
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    opened = await manager.upload_open("s1", "cancel.bin", 10, channel=channel)
    await manager.upload_chunk(
        "s1",
        opened["transfer_id"],
        0,
        base64.b64encode(b"12345").decode(),
        channel=channel,
    )
    assert await manager.upload_abort("s1", opened["transfer_id"], channel=channel)
    with pytest.raises(FsTransferError):
        await manager.upload_commit("s1", opened["transfer_id"], channel=channel)
    assert not (tmp_path / "cancel.bin").exists()
    assert manager.transfer_count() == 0


@pytest.mark.asyncio
async def test_upload_open_rejects_existing_target_and_escapes(tmp_path):
    (tmp_path / "exists.bin").write_bytes(b"old")
    manager = make_manager(HostWorkspace(root=str(tmp_path)))
    channel = FakeChannel()
    with pytest.raises(FsTransferError) as excinfo:
        await manager.upload_open("s1", "exists.bin", 3, channel=channel)
    assert excinfo.value.data["reason"] == "target_exists"
    # Overwrite is an explicit opt-in.
    opened = await manager.upload_open(
        "s1", "exists.bin", 3, overwrite=True, channel=channel
    )
    await manager.upload_chunk(
        "s1",
        opened["transfer_id"],
        0,
        base64.b64encode(b"new").decode(),
        channel=channel,
    )
    await manager.upload_commit("s1", opened["transfer_id"], channel=channel)
    assert (tmp_path / "exists.bin").read_bytes() == b"new"

    with pytest.raises(FsPathError):
        await manager.upload_open("s1", "../escape.bin", 3, channel=channel)
    with pytest.raises(FsPathError):
        await manager.upload_open("s1", "/abs.bin", 3, channel=channel)
    with pytest.raises(FsTransferError):
        await manager.upload_open("s1", "too-big.bin", 1 << 62, channel=channel)


@pytest.mark.asyncio
async def test_transfer_limit_per_connection(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"a")
    manager = make_manager(
        HostWorkspace(root=str(tmp_path)), max_transfers_per_connection=1
    )
    channel = FakeChannel()
    await manager.download_open("s1", "a.bin", channel=channel)
    with pytest.raises(FsTransferError) as excinfo:
        await manager.upload_open("s1", "b.bin", 1, channel=channel)
    assert excinfo.value.data["reason"] == "transfer_limit_exceeded"


# ---------------------------------------------------------------------------
#  Non-host substrates (tkt_z8T1amZ-GUs0)
# ---------------------------------------------------------------------------


class _BadEntryWorkspace(GenericWorkspace):
    """A substrate that mixes one contract-violating entry into a listing.

    ``FileEntry.path`` is workspace-relative on every backend shipped
    here, but a third-party ``Workspace`` (or a future backend regression)
    could still hand back an absolute path. One such entry used to abort
    the caller's entire ``fs_list`` with ``-32060 fs_path_invalid`` and
    kill a running watch's scan.
    """

    async def list_dir(self, path=".", **kwargs):
        entries = await self._inner.list_dir(path, **kwargs)
        entries.insert(
            0,
            FileEntry(
                name="rogue.txt",
                path=f"{self.root}/rogue.txt",
                is_dir=False,
                size=3,
            ),
        )
        return entries


@pytest.mark.asyncio
async def test_one_bad_entry_does_not_abort_a_generic_listing(tmp_path):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    manager = make_manager(_BadEntryWorkspace(root=str(tmp_path)))
    listing = await manager.list("s1", ".", recursive=True)
    assert [entry["path"] for entry in listing["entries"]] == ["keep.txt"]


@pytest.mark.asyncio
async def test_stat_reads_size_from_the_substrate_listing(tmp_path):
    """``fs_stat`` on a non-host substrate matches ``FileEntry.name``.

    That match only works because ``name`` is the bare basename on every
    backend; when ``SandboxWorkspace`` reported an absolute path there it
    never fired, and every sandbox file was advertised with ``size: null``
    — including to ``download_open``'s pre-read size cap.

    The file lives in a **subdirectory** on purpose: at the workspace root
    ``entry.name`` and ``entry.path`` are the same string, so matching on
    the wrong one passes anyway and the defect only shows up against a
    real container — which CI never runs.
    """
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "sized.bin").write_bytes(b"0123456789")
    manager = make_manager(GenericWorkspace(root=str(tmp_path)))
    info = await manager.stat("s1", "nested/sized.bin")
    assert info["type"] == "file"
    assert info["size"] == 10


@pytest.mark.asyncio
async def test_generic_scan_does_not_descend_symlinked_directories(tmp_path):
    """Mirrors the host scanner: ``is_symlink`` must be set by the backend."""
    outside = tmp_path.parent / "outside-generic"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.symlink(outside, root / "link")
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")
    manager = make_manager(GenericWorkspace(root=str(root)))
    listing = await manager.list("s1", ".", recursive=True)
    assert [entry["path"] for entry in listing["entries"]] == ["link"]


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_cls", [HostWorkspace, GenericWorkspace])
async def test_unaddressable_names_are_dropped_identically_on_both_scanners(
    tmp_path, workspace_cls
):
    """A name the wire cannot express is dropped on *every* substrate.

    ``back\\slash`` and ``C:`` are legal POSIX filenames — the workspace
    layer lists them — but ``canonical_relpath`` refuses both spellings,
    so a client could never stat or download such an entry. The host fast
    path used to emit them anyway while the generic path dropped them,
    which made the same tree report differently per substrate.
    """
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "back\\slash").mkdir()
    (tmp_path / "C:").mkdir()
    # Whitespace-padded names do not *raise* in canonicalization — they
    # come back *changed*. Advertising the changed spelling would invent a
    # filename that fs_stat then cannot find.
    (tmp_path / " lead.txt").write_text("lead", encoding="utf-8")
    (tmp_path / "trail.txt ").write_text("trail", encoding="utf-8")
    manager = make_manager(workspace_cls(root=str(tmp_path)))
    listing = await manager.list("s1", ".", recursive=True)
    assert [entry["path"] for entry in listing["entries"]] == ["keep.txt"]


@pytest.mark.asyncio
async def test_stat_does_not_advertise_a_symlinks_own_size(tmp_path):
    """``FileEntry.size`` is an lstat size; a download follows the link.

    Advertising the link's own length as the file's size would be a
    confidently wrong number — and would hand ``download_open``'s pre-read
    cap a value that lets an oversized target through.
    """
    (tmp_path / "big.bin").write_bytes(b"0" * 4096)
    try:
        os.symlink(tmp_path / "big.bin", tmp_path / "biglink")
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")
    info = await make_manager(GenericWorkspace(root=str(tmp_path))).stat(
        "s1", "biglink"
    )
    assert info["type"] == "file"
    assert info["size"] is None


@pytest.mark.asyncio
async def test_oversized_target_behind_a_symlink_is_still_refused(tmp_path):
    """The post-read check is the hard limit when nothing is advertised."""
    (tmp_path / "big.bin").write_bytes(b"0" * 4096)
    try:
        os.symlink(tmp_path / "big.bin", tmp_path / "biglink")
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")
    manager = make_manager(GenericWorkspace(root=str(tmp_path)), max_download_bytes=64)
    with pytest.raises(FsTransferError) as excinfo:
        await manager.download_open("s1", "biglink", channel=FakeChannel())
    assert excinfo.value.data["reason"] == "size_out_of_range"


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_cls", [HostWorkspace, GenericWorkspace])
@pytest.mark.parametrize("padded", [" lead.txt", "lead.txt "])
async def test_whitespace_padded_names_never_alias_onto_a_real_file(
    tmp_path, workspace_cls, padded
):
    """Two real files must never collapse into one snapshot entry.

    ``canonical_relpath`` strips surrounding whitespace, so `` lead.txt``
    and ``lead.txt`` canonicalize identically. Keying the snapshot on the
    canonical form made one of the two vanish outright — no log, no
    ``truncated`` flag — and left a watch flapping created/deleted as the
    alias won and lost.
    """
    # Both paddings, because a sorted listing hides the alias in one
    # direction: " lead.txt" sorts *before* "lead.txt" (so the real file
    # overwrites the alias and the bug looks benign) while "lead.txt "
    # sorts *after* it (so the padded file wins and 20 bytes are served
    # under a name that holds 4).
    (tmp_path / padded).write_bytes(b"x" * 20)
    (tmp_path / "lead.txt").write_bytes(b"y" * 4)
    manager = make_manager(workspace_cls(root=str(tmp_path)))
    listing = await manager.list("s1", ".", recursive=True)
    assert [entry["path"] for entry in listing["entries"]] == ["lead.txt"]
    # The surviving entry is the file that actually bears that name, not
    # the padded one wearing its spelling.
    assert [entry["size"] for entry in listing["entries"]] == [4]
    # And the advertised path really resolves.
    assert (await manager.stat("s1", "lead.txt"))["size"] == 4
