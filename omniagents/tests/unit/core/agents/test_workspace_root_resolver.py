"""AgentService.workspace_root_resolver — the authoritative cwd transform.

``_apply_session_metadata`` is the single point where ``workspace_root`` (the
agent's navigable cwd) is written onto a session. A configured resolver maps
the *requested* value to the *effective* one there, so e.g. a sandbox cwd is
always clamped to within the manifest root (a host path can never root a
sandbox run) while navigation among container subdirs is preserved.
"""

from __future__ import annotations

from unittest.mock import Mock

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session import Session
from omniagents.workspace import clamp_within


def _service(resolver) -> AgentService:
    spec = Mock(spec=AgentSpec)
    spec.name = "TestAgent"
    spec.model_name = "gpt-4"
    spec.use_safe_agent = False
    spec.max_turns = 10
    spec.project_slug = "p"
    spec.agent_slug = "a"
    svc = AgentService(spec, workspace_root_resolver=resolver)
    svc._session_manager = Mock()
    svc.channel = None
    return svc


def _session() -> Session:
    s = Session("p", "a")
    s.id = "sid"
    s.channels = []
    s.context = {}
    s.variables = {}
    return s


def _sandbox_resolver(root: str):
    # Mirrors omni serve's sandbox resolver: clamp the cwd to within the root.
    return lambda requested: clamp_within(requested, root)


def test_host_path_request_clamped_to_manifest_root():
    """The launcher anchors a host path; the resolver clamps it to the
    sandbox boundary so it never roots a sandbox run."""
    svc = _service(_sandbox_resolver("/workspace/proj"))
    s = _session()
    svc._apply_session_metadata(s, workspace_root="/home/host/leaked")
    assert s.variables["workspace_root"] == "/workspace/proj"
    assert s.context["workspace_root"] == "/workspace/proj"


def test_container_subdir_request_is_preserved_for_navigation():
    """A cwd within the manifest root is kept — sandbox navigation works."""
    svc = _service(_sandbox_resolver("/workspace/proj"))
    s = _session()
    svc._apply_session_metadata(s, workspace_root="/workspace/proj/src")
    assert s.variables["workspace_root"] == "/workspace/proj/src"


def test_empty_request_defaults_to_root():
    svc = _service(_sandbox_resolver("/workspace/proj"))
    s = _session()
    svc._apply_session_metadata(s, workspace_root=None)
    assert s.variables["workspace_root"] == "/workspace/proj"


def test_resolver_applies_to_existing_context_value():
    """When start_run doesn't re-pass workspace_root, the resolver still runs
    against whatever the session already carries (e.g. set by session.ensure)
    — so a previously-anchored host path gets clamped on the next run."""
    svc = _service(_sandbox_resolver("/workspace/proj"))
    s = _session()
    s.context = {"workspace_root": "/home/host/leaked"}
    svc._apply_session_metadata(
        s, context=s.context, context_provided=True, workspace_root=None
    )
    assert s.context["workspace_root"] == "/workspace/proj"
    assert s.variables["workspace_root"] == "/workspace/proj"


def test_host_resolver_allows_any_dir():
    """A host-style resolver passes navigable host dirs through unchanged."""
    svc = _service(lambda requested: requested or "/home/user/proj")
    s = _session()
    svc._apply_session_metadata(s, workspace_root="/home/user/elsewhere")
    assert s.variables["workspace_root"] == "/home/user/elsewhere"


def test_no_resolver_keeps_client_value():
    svc = _service(None)
    s = _session()
    svc._apply_session_metadata(s, workspace_root="/home/host/local")
    assert s.variables["workspace_root"] == "/home/host/local"
