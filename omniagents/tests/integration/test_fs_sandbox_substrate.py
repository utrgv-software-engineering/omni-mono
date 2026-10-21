"""``fs_*`` against a real Docker sandbox workspace (tkt_z8T1amZ-GUs0).

Every other ``fs_*`` test drives :class:`HostWorkspace`, which its own
reviewer flagged at the time as the unverified area — and it was. Against
a real container the surface was unusable:

* ``fs_list`` and ``fs_watch`` raised ``-32060 fs_path_invalid`` on the
  *first* entry, because ``SandboxWorkspace`` reported the SDK's absolute
  container path in ``FileEntry.path`` and the scanner canonicalizes wire
  paths as workspace-relative;
* ``fs_stat`` advertised ``size: null`` for every file, because the size
  lookup matches ``FileEntry.name`` against a basename and ``name`` was
  also the absolute path — which also meant ``fs_download_open``'s
  "check the advertised size before reading the file into memory" cap was
  never enforced on this substrate;
* every entry claimed ``is_dir=True`` (the SDK's ``is_dir`` is a method,
  so the old ``bool(getattr(...))`` was always truthy) and never
  ``is_symlink``, so the scanner would have walked into symlinked
  directories.

Skipped unless Docker is reachable and the image is already present, so
it never pulls itself — same posture as ``test_git_sandbox_substrate.py``,
and CI provisions the image in the ``sandbox-substrate`` job where
``OMNIAGENTS_REQUIRE_SANDBOX_IMAGE`` turns the skip into a hard failure
(tkt_k5Yk_JBkrIw6).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any, Dict, List, Optional, Tuple

import pytest
import pytest_asyncio

from omniagents.rpc.filesystem import (
    FilesystemManager,
    FsNotFoundError,
    FsPathError,
    FsTransferError,
)
from omniagents.workspace.sandbox import SandboxWorkspace
from omniagents.workspace.types import WorkspaceEscapeError
from tests.integration._sandbox_image import image_available

IMAGE = "alpine/git:latest"
TIMEOUT = 30.0


pytestmark = pytest.mark.skipif(
    not image_available(IMAGE),
    reason=f"docker image {IMAGE} is not available locally",
)


class FakeChannel:
    def __init__(self) -> None:
        self.notifications: List[Tuple[str, Dict[str, Any]]] = []

    async def async_call(self, method: str, args: Optional[Dict[str, Any]] = None):
        self.notifications.append((method, args or {}))

    def by_method(self, method: str) -> List[Dict[str, Any]]:
        return [payload for name, payload in self.notifications if name == method]

    def event_paths(self, event_type: Optional[str] = None) -> set:
        return {
            event["path"]
            for frame in self.by_method("fs_events")
            for event in frame["events"]
            if event_type is None or event["type"] == event_type
        }


async def wait_for(predicate, timeout: float = TIMEOUT) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_poll(), timeout)


@pytest_asyncio.fixture
async def sandbox_workspace():
    from omniagents.core.sandbox import build_sandbox_client

    client, options = build_sandbox_client(
        {"client": {"type": "docker"}, "options": {"image": IMAGE}}
    )
    session = await client.create(options=options)
    try:
        yield SandboxWorkspace(session=session, root="/workspace")
    finally:
        try:
            await client.delete(session)
        except Exception:  # pragma: no cover - teardown is best effort
            pass


@pytest_asyncio.fixture
async def populated(sandbox_workspace):
    """A container tree with every awkward entry type the scanner can meet."""
    result = await sandbox_workspace.exec(
        "mkdir -p /workspace/sub && "
        "printf 'hello\\n' > /workspace/a.txt && "
        "printf 'nested\\n' > /workspace/sub/n.txt && "
        "printf 'dot\\n' > /workspace/.dot && "
        "printf 'secret\\n' > /outside_secret.txt && "
        "ln -sf /etc /workspace/dirlink && "
        "ln -sf /outside_secret.txt /workspace/filelink && "
        "ln -sf /workspace/nowhere /workspace/dangling && "
        "mkfifo /workspace/fifo",
        shell=True,
        timeout=60,
    )
    assert result.exit_code == 0, result.stderr
    return sandbox_workspace


def _manager(workspace, **overrides) -> FilesystemManager:
    async def resolver(session_id: str):
        return workspace

    defaults: Dict[str, Any] = dict(
        workspace_resolver=resolver,
        min_poll_interval=0.05,
        poll_interval_generic=0.2,
        progress_interval=0.0,
    )
    defaults.update(overrides)
    return FilesystemManager(**defaults)


# ---------------------------------------------------------------------------
#  The workspace layer itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_dir_reports_relative_paths_and_real_types(populated):
    entries = {e.name: e for e in await populated.list_dir(".", include_hidden=True)}
    assert set(entries) == {
        ".dot",
        "a.txt",
        "dangling",
        "dirlink",
        "fifo",
        "filelink",
        "sub",
    }
    assert entries["a.txt"].path == "a.txt"
    assert entries["a.txt"].is_dir is False
    assert entries["a.txt"].size == 6
    assert entries["sub"].is_dir is True
    assert entries["sub"].size is None
    assert entries["dirlink"].is_symlink is True
    assert entries["dirlink"].is_dir is False
    assert entries["dangling"].is_symlink is True
    assert entries["fifo"].is_dir is False

    nested = await populated.list_dir("sub")
    assert [(e.name, e.path) for e in nested] == [("n.txt", "sub/n.txt")]
    # The contract's promise: entry.path addresses the entry again.
    assert await populated.read_text(nested[0].path) == "nested\n"


@pytest.mark.asyncio
async def test_list_dir_hides_dotfiles_by_default(populated):
    assert ".dot" not in {e.name for e in await populated.list_dir(".")}


@pytest.mark.asyncio
async def test_list_dir_raises_os_errors_not_transport_errors(populated):
    with pytest.raises(FileNotFoundError):
        await populated.list_dir("nope")
    with pytest.raises(NotADirectoryError):
        await populated.list_dir("a.txt")


# ---------------------------------------------------------------------------
#  fs_list / fs_stat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_list_returns_workspace_relative_entries(populated):
    listing = await _manager(populated).list("s1", ".", recursive=True)
    assert listing["writable"] is True
    by_path = {entry["path"]: entry for entry in listing["entries"]}
    # Recursion happened (sub was descended) and no absolute path leaked.
    assert "sub/n.txt" in by_path
    assert all(not path.startswith("/") for path in by_path)
    assert by_path["a.txt"] == {
        "path": "a.txt",
        "type": "file",
        "size": 6,
        "mtime": None,
        "writable": True,
    }
    assert by_path["sub"]["type"] == "directory"
    # The symlinked directory is listed but never descended into: nothing
    # from /etc may appear in a workspace listing.
    assert "dirlink" in by_path
    assert not any(path.startswith("dirlink/") for path in by_path)


@pytest.mark.asyncio
async def test_fs_stat_reports_the_real_size(populated):
    info = await _manager(populated).stat("s1", "sub/n.txt")
    assert info["path"] == "sub/n.txt"
    assert info["type"] == "file"
    assert info["size"] == 7
    assert info["mime"] == "text/plain"


# ---------------------------------------------------------------------------
#  fs_watch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_watch_fires_on_create_and_delete(populated):
    manager = _manager(populated)
    channel = FakeChannel()
    watch = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=200, channel=channel
    )
    assert watch["mechanism"] == "poll:workspace"
    try:
        await populated.write_text("sub/fresh.txt", "fresh\n")
        await wait_for(lambda: "sub/fresh.txt" in channel.event_paths("created"))
        await populated.remove("sub/fresh.txt")
        await wait_for(lambda: "sub/fresh.txt" in channel.event_paths("deleted"))
    finally:
        await manager.unwatch("s1", watch["watch_id"], channel=channel)


@pytest.mark.asyncio
async def test_fs_watch_fires_when_a_file_changes_size(populated):
    manager = _manager(populated)
    channel = FakeChannel()
    watch = await manager.watch(
        "s1", ".", recursive=True, poll_interval_ms=200, channel=channel
    )
    try:
        await populated.write_text("a.txt", "hello and then some more\n")
        await wait_for(lambda: "a.txt" in channel.event_paths("modified"))
    finally:
        await manager.unwatch("s1", watch["watch_id"], channel=channel)


# ---------------------------------------------------------------------------
#  Chunked transfers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_then_download_round_trips_with_checksum(populated):
    manager = _manager(populated, chunk_size=1024)
    channel = FakeChannel()
    payload = bytes(range(256)) * 40  # 10 KiB, several chunks
    digest = hashlib.sha256(payload).hexdigest()

    opened = await manager.upload_open(
        "s1", "uploads/blob.bin", len(payload), sha256=digest, channel=channel
    )
    sent = 0
    while sent < len(payload):
        chunk = payload[sent : sent + manager.chunk_size]
        await manager.upload_chunk(
            "s1",
            opened["transfer_id"],
            sent,
            base64.b64encode(chunk).decode("ascii"),
            channel=channel,
        )
        sent += len(chunk)
    committed = await manager.upload_commit(
        "s1", opened["transfer_id"], channel=channel
    )
    assert committed == {
        "path": "uploads/blob.bin",
        "size": len(payload),
        "sha256": digest,
    }
    # It really landed in the container, at the relative path we asked for.
    assert await populated.read_bytes("uploads/blob.bin") == payload

    handle = await manager.download_open("s1", "uploads/blob.bin", channel=channel)
    assert handle["size"] == len(payload)
    assert handle["sha256"] == digest
    received = b""
    while True:
        frame = await manager.download_read(
            "s1", handle["transfer_id"], channel=channel
        )
        received += base64.b64decode(frame["data"])
        if frame["eof"]:
            break
    assert hashlib.sha256(received).hexdigest() == digest
    assert await manager.download_close("s1", handle["transfer_id"], channel=channel)


@pytest.mark.asyncio
async def test_download_is_refused_above_the_cap_before_reading(populated):
    """The pre-read cap uses ``fs_stat``'s size, which was ``None`` before."""
    await populated.write_text("big.bin", "x" * 4096)
    manager = _manager(populated, max_download_bytes=64)
    with pytest.raises(FsTransferError) as excinfo:
        await manager.download_open("s1", "big.bin", channel=FakeChannel())
    assert excinfo.value.data["reason"] == "size_out_of_range"


@pytest.mark.asyncio
async def test_download_serves_from_the_staged_copy_after_a_symlink_swap(populated):
    """TOCTOU: the path is resolved once, at open."""
    await populated.write_text("swap.txt", "original\n")
    manager = _manager(populated, chunk_size=4)
    channel = FakeChannel()
    handle = await manager.download_open("s1", "swap.txt", channel=channel)
    swap = await populated.exec(
        "rm -f /workspace/swap.txt && ln -sf /outside_secret.txt /workspace/swap.txt",
        shell=True,
        timeout=30,
    )
    assert swap.exit_code == 0, swap.stderr
    received = b""
    while True:
        frame = await manager.download_read(
            "s1", handle["transfer_id"], channel=channel
        )
        received += base64.b64decode(frame["data"])
        if frame["eof"]:
            break
    assert received == b"original\n"


# ---------------------------------------------------------------------------
#  Confinement — the whole point of normalizing paths carefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "../outside_secret.txt",
        "/outside_secret.txt",
        "sub/../../outside_secret.txt",
        "/etc/hostname",
        "C:/windows/system32",
        "sub\\n.txt",
    ],
)
async def test_wire_paths_that_leave_the_workspace_are_refused(populated, path):
    manager = _manager(populated)
    channel = FakeChannel()
    with pytest.raises(FsPathError):
        await manager.stat("s1", path)
    with pytest.raises(FsPathError):
        await manager.list("s1", path)
    with pytest.raises(FsPathError):
        await manager.download_open("s1", path, channel=channel)
    with pytest.raises(FsPathError):
        await manager.upload_open("s1", path, 1, channel=channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["filelink", "dirlink/hostname"])
async def test_symlinks_pointing_outside_are_never_served(populated, path):
    """A symlinked entry *and* a symlinked parent, both refused.

    The typed code differs from the host by design: ``HostWorkspace``
    realpaths inside ``resolve`` and answers ``-32060 fs_path_invalid``,
    while on a sandbox the predicate honestly cannot see a file outside
    the workspace and the surface answers ``-32061 fs_not_found``. Both
    are refusals and neither serves a byte; see
    ``test_reading_outside_the_root_is_a_typed_escape`` for the typed
    escape the workspace layer raises when the substrate is actually
    asked to touch the path.
    """
    manager = _manager(populated)
    with pytest.raises((FsPathError, FsNotFoundError)):
        await manager.download_open("s1", path, channel=FakeChannel())


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["filelink", "dirlink/hostname"])
async def test_reading_outside_the_root_is_a_typed_escape(populated, path):
    """The SDK's own path-policy rejection is translated, not leaked.

    Before this, callers saw the SDK's ``InvalidManifestPathError``
    (message: "manifest path must be relative") — a transport exception
    the ``fs_*``/``git_*`` surfaces have no typed mapping for, so it
    surfaced as an untyped ``-32603`` echoing container internals.
    """
    with pytest.raises(WorkspaceEscapeError):
        await populated.read_bytes(path)


@pytest.mark.asyncio
async def test_a_symlinked_parent_is_refused_for_writes_too(populated):
    manager = _manager(populated)
    channel = FakeChannel()
    opened = await manager.upload_open(
        "s1", "dirlink/planted.txt", 2, overwrite=True, channel=channel
    )
    await manager.upload_chunk(
        "s1",
        opened["transfer_id"],
        0,
        base64.b64encode(b"hi").decode("ascii"),
        channel=channel,
    )
    with pytest.raises(FsPathError):
        await manager.upload_commit("s1", opened["transfer_id"], channel=channel)
    probe = await populated.exec("cat /etc/planted.txt", shell=True, timeout=30)
    assert probe.exit_code != 0


@pytest.mark.asyncio
async def test_dangling_symlinks_and_fifos_do_not_break_anything(populated):
    manager = _manager(populated)
    listing = await manager.list("s1", ".", recursive=True)
    paths = {entry["path"] for entry in listing["entries"]}
    assert {"dangling", "fifo"} <= paths
    # A dangling link points at nothing, so the surface refuses it with a
    # typed fs_* error rather than serving bytes or raising something the
    # client has no mapping for.
    with pytest.raises((FsPathError, FsNotFoundError)):
        await manager.download_open("s1", "dangling", channel=FakeChannel())
    with pytest.raises((FsPathError, FsNotFoundError)):
        await manager.download_open("s1", "fifo", channel=FakeChannel())


@pytest.mark.asyncio
async def test_workspace_resolve_refuses_container_absolute_escapes(populated):
    for path in ("../etc", "/etc/hostname", "sub/../../etc"):
        with pytest.raises(WorkspaceEscapeError):
            populated.resolve(path)
    assert populated.resolve("/workspace/sub/n.txt") == "/workspace/sub/n.txt"
    assert await populated.exists("../outside_secret.txt") is False
    assert await populated.is_file("/outside_secret.txt") is False
    # A symlink whose target leaves the root answers False here exactly as
    # it does on the host, where resolve() realpaths before answering.
    assert await populated.is_file("filelink") is False
    assert await populated.is_dir("dirlink") is False


@pytest.mark.asyncio
async def test_awkward_directory_names_do_not_break_listings(populated):
    """Names an agent can create that no wire policy can express.

    ``list_dir`` must report them (they are legal POSIX filenames), while
    the ``fs_*`` wire drops them rather than offering a client an entry it
    could never address — and neither may take the rest of the tree down
    with it. Rejecting them in the workspace layer instead made the files
    silently vanish on this substrate only.
    """
    result = await populated.exec(
        "cd /workspace && mkdir -p 'C:' 'E:migration.sql' 'back\\slash'",
        shell=True,
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr

    names = {e.name for e in await populated.list_dir(".")}
    assert {"C:", "E:migration.sql", "back\\slash"} <= names
    assert "a.txt" in names

    listing = await _manager(populated).list("s1", ".", recursive=False)
    paths = {entry["path"] for entry in listing["entries"]}
    assert "a.txt" in paths and "sub" in paths
    assert not any("\\" in path or path.startswith("C:") for path in paths)


@pytest.mark.asyncio
async def test_root_spelling_does_not_change_behavior(sandbox_workspace):
    """``root="/workspace/"`` must behave exactly like ``"/workspace"``."""
    from omniagents.workspace.sandbox import SandboxWorkspace

    await sandbox_workspace.write_text("marker.txt", "x\n")
    trailing = SandboxWorkspace(session=sandbox_workspace._session, root="/workspace/")
    assert trailing.root == "/workspace"
    assert trailing.resolve(".") == "/workspace"
    assert trailing.resolve("marker.txt") == "/workspace/marker.txt"
    assert "marker.txt" in {e.name for e in await trailing.list_dir(".")}
    assert [e.path for e in await trailing.list_dir(".")] == ["marker.txt"]


@pytest.mark.asyncio
async def test_stat_does_not_advertise_a_symlinks_own_size(populated):
    """An in-workspace symlink must not advertise the link's length."""
    result = await populated.exec(
        "cd /workspace && dd if=/dev/zero of=big.bin bs=1024 count=64 2>/dev/null && "
        "ln -sf /workspace/big.bin biglink",
        shell=True,
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr
    manager = _manager(populated)
    assert (await manager.stat("s1", "big.bin"))["size"] == 65536
    assert (await manager.stat("s1", "biglink"))["size"] is None
    # And the post-read cap still refuses the oversized target.
    capped = _manager(populated, max_download_bytes=1024)
    with pytest.raises(FsTransferError):
        await capped.download_open("s1", "biglink", channel=FakeChannel())


@pytest.mark.asyncio
async def test_predicates_fail_closed_when_readlink_is_unavailable(populated):
    """The confinement probe must not degrade to a lexical check.

    ``exists``/``is_file``/``is_dir`` realpath their argument in-container
    before answering, so a link out of the workspace answers ``False``
    exactly as it does on the host. An earlier version fell back to the
    *lexical* path when ``readlink`` failed — and the lexical path of a
    symlink is inside the root by construction, so ``test -f`` would then
    happily follow the link and answer for a file outside the workspace.

    No image in this suite lacks ``readlink``, so nothing else here can
    reach that branch; this test builds the scenario by shadowing
    ``readlink`` with a stub that exits non-zero. Reverting the guard to
    the old ``|| printf %s`` fallback turns every assertion below from
    ``False`` into ``True``.
    """
    outside = "/outside_secret.txt"
    result = await populated.exec(
        f"ln -sf {outside} /workspace/escaping_file && "
        "ln -sf /etc /workspace/escaping_dir",
        shell=True,
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr
    # Sanity: with a working readlink these are already refused, and a
    # legitimate in-workspace file is still visible.
    assert await populated.is_file("escaping_file") is False
    assert await populated.is_dir("escaping_dir") is False
    assert await populated.is_file("a.txt") is True

    broke = await populated.exec(
        "for p in $(command -v readlink; ls /bin/readlink /usr/bin/readlink "
        '2>/dev/null); do rm -f "$p"; printf \'#!/bin/sh\\nexit 127\\n\' > "$p"; '
        'chmod 755 "$p"; done; readlink -f /workspace/a.txt',
        shell=True,
        timeout=30,
    )
    if broke.exit_code == 0:  # pragma: no cover - image-specific
        pytest.skip("could not shadow readlink in this image")

    try:
        # The security property: a path whose target leaves the workspace
        # is refused even though the probe cannot resolve it.
        assert await populated.exists("escaping_file") is False
        assert await populated.is_file("escaping_file") is False
        assert await populated.exists("escaping_dir") is False
        assert await populated.is_dir("escaping_dir") is False
        # The documented cost of failing closed: with no way to prove
        # confinement, even an ordinary in-workspace file answers False.
        # Refusing to answer is the correct failure mode here — answering
        # from the lexical path is what let the two above through.
        assert await populated.is_file("a.txt") is False
    finally:
        restored = await populated.exec(
            "rm -f /usr/bin/readlink && ln -sf /bin/busybox /usr/bin/readlink",
            shell=True,
            timeout=30,
        )
        assert restored.exit_code == 0, restored.stderr
    # Restoring readlink restores normal answers, proving the stub — not
    # some unrelated breakage — caused the refusals above.
    assert await populated.is_file("a.txt") is True
    assert await populated.is_file("escaping_file") is False


@pytest.mark.asyncio
async def test_whitespace_padded_twins_are_never_confused(populated):
    """The container-side version of the ``ls -la`` leading-space loss.

    ``" lead.txt"`` and ``"lead.txt"`` both come back from the SDK's
    listing as ``lead.txt``, with the padded file's size attached to one
    of the rows. Before this, ``fs_stat("lead.txt")`` served 20 for a file
    holding 4 bytes. Both rows are now dropped: a wrong size is worse than
    an absent entry, and nothing in the substrate's output can tell them
    apart.
    """
    result = await populated.exec(
        "cd /workspace && printf '%020d' 0 > ' lead.txt' && printf yyyy > lead.txt && "
        "printf 'zzzzzzzzz' > 'trail.txt '",
        shell=True,
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr

    names = [e.name for e in await populated.list_dir(".")]
    # Neither twin is reported, and no name is reported twice.
    assert names.count("lead.txt") == 0
    assert len(names) == len(set(names))
    # A padded name that has no twin is still listed truthfully by the
    # workspace layer (the wire drops it separately — see the fs_list
    # assertion below).
    assert "trail.txt " in names

    manager = _manager(populated)
    listing = await manager.list("s1", ".", recursive=False)
    paths = {entry["path"] for entry in listing["entries"]}
    assert "lead.txt" not in paths
    assert "trail.txt" not in paths and "trail.txt " not in paths
    assert "a.txt" in paths
    # ``fs_stat`` can still confirm the real ``lead.txt`` exists — the
    # predicate resolves the exact path rather than reading the listing —
    # but it now reports ``null`` ("unknown") instead of the padded twin's
    # 20 bytes. An absent size is recoverable; a wrong one is not.
    info = await manager.stat("s1", "lead.txt")
    assert info["type"] == "file"
    assert info["size"] is None
