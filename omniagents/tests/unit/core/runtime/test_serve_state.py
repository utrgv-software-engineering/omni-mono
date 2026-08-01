"""Tests for the process-global serve-session state."""

import pytest

from omniagents.core.runtime import serve_state
from omniagents.core.runtime.serve_state import SourceDescriptor, SwitchContext


@pytest.fixture(autouse=True)
def _reset():
    serve_state.reset()
    yield
    serve_state.reset()


# ----------------------------- simple slots ----------------------------- #


def test_all_slots_default_to_absent():
    assert serve_state.get_manifest_root() is None
    assert serve_state.get_sources() == []
    assert serve_state.get_source_descriptors() == []
    assert serve_state.get_project_context() is None
    assert serve_state.get_orchestrator_spec() is None
    assert serve_state.get_sandbox_session() is None
    assert serve_state.get_discard_snapshot_on_exit() is False
    assert serve_state.get_switch_context() is None


def test_slots_round_trip_and_clear():
    ctx = object()
    spec = object()
    session = object()
    serve_state.set_manifest_root("/workspace")
    serve_state.set_project_context(ctx)
    serve_state.set_orchestrator_spec(spec)
    serve_state.set_sandbox_session(session)
    serve_state.set_discard_snapshot_on_exit(True)

    assert serve_state.get_manifest_root() == "/workspace"
    assert serve_state.get_project_context() is ctx
    assert serve_state.get_orchestrator_spec() is spec
    assert serve_state.get_sandbox_session() is session
    assert serve_state.get_discard_snapshot_on_exit() is True

    serve_state.reset()
    assert serve_state.get_manifest_root() is None
    assert serve_state.get_sandbox_session() is None
    assert serve_state.get_discard_snapshot_on_exit() is False


# ----------------------------- sources ---------------------------------- #


def test_sources_round_trip_preserves_unknown_keys():
    published = [{"kind": "local", "mountName": "app", "path": "/host/app", "extra": 1}]
    serve_state.set_sources(published)
    assert serve_state.get_sources() == published


def test_get_sources_returns_copy():
    serve_state.set_sources([{"kind": "local", "mountName": "app"}])
    out = serve_state.get_sources()
    out.append({"kind": "junk"})
    assert len(serve_state.get_sources()) == 1


def test_set_sources_none_resets():
    serve_state.set_sources([{"kind": "local", "mountName": "app"}])
    serve_state.set_sources(None)
    assert serve_state.get_sources() == []


def test_set_sources_accepts_typed_descriptors():
    serve_state.set_sources(
        [SourceDescriptor(kind="git-remote", mount_name="lib", repo_url="u", ref="r")]
    )
    assert serve_state.get_sources() == [
        {
            "kind": "git-remote",
            "mountName": "lib",
            "writable": True,
            "repoUrl": "u",
            "ref": "r",
        }
    ]


def test_get_source_descriptors_parses_published_dicts():
    serve_state.set_sources(
        [
            {"kind": "local-git", "mountName": "app", "path": "/host/app"},
            {"kind": "git-remote", "mountName": "lib", "repoUrl": "u"},
            {"mountName": "  ", "path": 42},  # junk fields tolerated
        ]
    )
    descs = serve_state.get_source_descriptors()
    assert descs[0] == SourceDescriptor(
        kind="local-git", mount_name="app", path="/host/app"
    )
    assert descs[0].is_local and descs[0].is_git
    assert descs[0].local_path() == "/host/app"
    assert descs[1].local_path() is None
    assert not descs[1].is_local and descs[1].is_git
    assert descs[2] == SourceDescriptor()


def test_source_descriptor_dict_round_trip():
    d = {
        "kind": "local",
        "mountName": "app",
        "writable": False,
        "path": "/host/app",
    }
    assert SourceDescriptor.from_dict(d).to_dict() == d


@pytest.mark.parametrize(
    "raw", [{}, {"writable": None}, {"writable": 1}, {"writable": "false"}]
)
def test_source_descriptor_writable_defaults_true_for_missing_or_junk(raw):
    assert SourceDescriptor.from_dict(raw).writable is True


def test_source_descriptor_writable_respects_explicit_boolean():
    assert SourceDescriptor.from_dict({"writable": True}).writable is True
    assert SourceDescriptor.from_dict({"writable": False}).writable is False


# ----------------------------- switch context ---------------------------- #


def _switch_ctx() -> SwitchContext:
    return SwitchContext(
        app=object(),
        spec=object(),
        session_id="s1",
        snapshot_dir=None,
        sources_parsed=[],
        credentials_parsed=[],
        workspace_dir="/w",
        project_id=None,
        host="127.0.0.1",
        port=1234,
        auth_token=None,
    )


def test_switch_context_publish_and_clear():
    ctx = _switch_ctx()
    serve_state.set_switch_context(ctx)
    assert serve_state.get_switch_context() is ctx
    assert ctx.switching is False
    assert ctx.ready_event.is_set() is False
    serve_state.set_switch_context(None)
    assert serve_state.get_switch_context() is None
