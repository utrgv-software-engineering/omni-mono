"""Exercise resume source reconciliation against a real temporary filesystem."""

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.sandbox.manifest import Manifest

from omniagents.core.runtime.serve_harness import _reconcile_resumed_sources
from omniagents.core.sandbox import LocalGitArchive


class State(SimpleNamespace):
    def model_copy(self, *, update):
        return State(**(vars(self) | update))


class Session:
    def __init__(self, manifest):
        self.state = State(manifest=manifest)
        self.applied = []
        self.fail_source = None

    def _manifest_base_dir(self):
        return Path.cwd()

    async def exec(self, command, *, shell):
        result = await asyncio.to_thread(
            subprocess.run, command, shell=shell, capture_output=True
        )
        return SimpleNamespace(exit_code=result.returncode)

    async def _apply_entry_batch(self, entries, *, base_dir):
        for destination, entry in entries:
            self.applied.append(entry.src.name)
            if entry.src.name == self.fail_source:
                destination.mkdir()
                raise RuntimeError("source unavailable")
            shutil.copytree(entry.src, destination)


def fixture(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    old = tmp_path / "original"
    old.mkdir()
    (old / "file").write_text("original")
    shutil.copytree(old, root / "old")
    (root / "old/file").write_text("uncommitted agent work")
    new = tmp_path / "new"
    new.mkdir()
    (new / "syllabus.md").write_text("next lecture")
    previous = Manifest(root=str(root), entries={"old": LocalGitArchive(src=old)})
    desired = previous.model_copy(
        update={"entries": previous.entries | {"new": LocalGitArchive(src=new)}}
    )
    return root, Session(previous), desired


@pytest.mark.asyncio
async def test_adds_repository_preserves_work_and_is_idempotent(tmp_path):
    root, session, desired = fixture(tmp_path)
    await _reconcile_resumed_sources(session, desired)
    assert (root / "new/syllabus.md").read_text() == "next lecture"
    assert (root / "old/file").read_text() == "uncommitted agent work"
    assert (root / "new").stat().st_uid == root.stat().st_uid
    await _reconcile_resumed_sources(session, desired)
    assert session.applied == ["new"]
    assert session.state.manifest.entries == desired.entries
    assert not list(root.glob(".omni-source-*"))


@pytest.mark.asyncio
async def test_collision_fails_without_overwriting_files(tmp_path):
    root, session, desired = fixture(tmp_path)
    (root / "new").mkdir()
    (root / "new/file").write_text("keep me")
    with pytest.raises(ValueError, match="overwrite"):
        await _reconcile_resumed_sources(session, desired)
    assert (root / "new/file").read_text() == "keep me"
    assert session.applied == []


@pytest.mark.asyncio
async def test_failure_preserves_completed_sources_for_retry(tmp_path):
    root, session, desired = fixture(tmp_path)
    broken = tmp_path / "broken"
    broken.mkdir()
    desired = desired.model_copy(
        update={"entries": desired.entries | {"broken": LocalGitArchive(src=broken)}}
    )
    session.fail_source = "broken"
    with pytest.raises(RuntimeError, match="unavailable"):
        await _reconcile_resumed_sources(session, desired)
    assert "new" in session.state.manifest.entries
    assert "broken" not in session.state.manifest.entries
    assert not list(root.glob(".omni-source-*"))
    session.fail_source = None
    await _reconcile_resumed_sources(session, desired)
    assert session.applied == ["new", "broken", "broken"]


@pytest.mark.asyncio
async def test_changed_source_fails_without_reseeding(tmp_path):
    root, session, desired = fixture(tmp_path)
    desired = desired.model_copy(update={"entries": {"old": desired.entries["new"]}})
    with pytest.raises(ValueError, match="changed or was removed"):
        await _reconcile_resumed_sources(session, desired)
    assert (root / "old/file").read_text() == "uncommitted agent work"
    assert session.applied == []


@pytest.mark.asyncio
@pytest.mark.parametrize("same_container", [True, False])
async def test_resume_materializes_additions_before_services(
    monkeypatch, tmp_path, same_container
):
    from unittest.mock import AsyncMock, Mock
    from omniagents.core.runtime import serve_harness

    root, session, desired = fixture(tmp_path)
    session.state.container_id = "original" if same_container else "replacement"
    session.start = AsyncMock()
    client = SimpleNamespace(
        resume=AsyncMock(return_value=session),
        deserialize_session_state=lambda state: state,
    )
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    monkeypatch.setattr(
        serve_harness,
        "_read_session_state",
        lambda path: {
            "container_id": "original",
            "session_state": {},
        },
    )
    monkeypatch.setattr(
        serve_harness, "_build_sandbox", lambda profile, sources: (client, {}, desired)
    )
    monkeypatch.setattr(serve_harness, "_build_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(serve_harness, "_configure_credentials", AsyncMock())
    init = AsyncMock()
    monkeypatch.setattr(serve_harness, "_run_init", init)

    async def services(*args):
        assert (root / "new/syllabus.md").is_file()
        return []

    monkeypatch.setattr(serve_harness, "_start_services", services)
    monkeypatch.setattr(
        serve_harness, "_resolve_service_urls", AsyncMock(return_value={})
    )
    saved = Mock()
    monkeypatch.setattr(serve_harness, "write_session_state", saved)
    profile = SimpleNamespace(raw={}, build=[], init=["initialize"], services=[])
    await serve_harness.bring_up_session(
        profile=profile,
        sources_parsed=[],
        snapshot_ref="resident",
        snapshot_dir=None,
        state_path=state_path,
    )
    assert init.await_count == (0 if same_container else 1)
    assert saved.call_args.args[2].state.manifest.entries == desired.entries
    assert (root / "old/file").read_text() == "uncommitted agent work"


@pytest.mark.asyncio
async def test_durable_resume_retains_deselected_repositories(tmp_path):
    root, session, desired = fixture(tmp_path)
    desired = desired.model_copy(update={"entries": {"new": desired.entries["new"]}})
    await _reconcile_resumed_sources(session, desired, retain_removed=True)
    assert (root / "old/file").read_text() == "uncommitted agent work"
    assert (root / "new/syllabus.md").is_file()
    assert set(session.state.manifest.entries) == {"old", "new"}
    await _reconcile_resumed_sources(session, desired, retain_removed=True)
    assert session.applied == ["new"]


@pytest.mark.asyncio
async def test_durable_resume_refuses_replacement_container(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from omniagents.core.runtime import serve_harness

    root, session, desired = fixture(tmp_path)
    session.state.container_id = "unexpected-replacement"
    session.start = AsyncMock()
    client = SimpleNamespace(
        resume=AsyncMock(return_value=session),
        deserialize_session_state=lambda state: state,
    )
    state = tmp_path / "state.json"
    state.write_text(
        '{"container_id":"original","session_state":{"container_id":"original"}}'
    )
    monkeypatch.setattr(
        serve_harness, "_build_sandbox", lambda *a, **k: (client, {}, desired)
    )
    monkeypatch.setattr(serve_harness, "_build_snapshot", lambda **k: None)
    cleanup = AsyncMock()
    monkeypatch.setattr(serve_harness, "teardown_session", cleanup)
    with pytest.raises(RuntimeError, match="missing"):
        await serve_harness.bring_up_session(
            profile=SimpleNamespace(raw={}, build=[]),
            sources_parsed=[],
            snapshot_ref="personal",
            snapshot_dir=None,
            state_path=state,
            durable=True,
        )
    cleanup.assert_awaited_once_with(session, [], destroy=True)
    session.start.assert_not_awaited()
    assert "original" in state.read_text()
