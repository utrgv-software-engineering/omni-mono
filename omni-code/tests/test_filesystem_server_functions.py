"""fs_* / terminal.* wiring tests.

The server functions themselves are framework builtins
(``omniagents.builtin.server_functions.workspace_info`` and ``.terminal``,
activated by the builtin ``workspace_info`` / ``terminal`` capability
packs). What omni-code owns — and what these tests cover — is the omni
agent's activation of those packs and the serve-session state ``omni
serve`` publishes into ``omniagents.core.runtime.serve_state``, which the
``omniagents.core.runtime.workspace_info`` seams read by default (no
providers installed).

The old omni-code ``session_ensure`` shim (a proxy for the framework's own
builtin ``session.ensure``) was deleted with the migration.
"""

from pathlib import Path

from omniagents.core.runtime import serve_state as aws
from omniagents.core.runtime import workspace_info as ws_seams

# Importing the omni context module installs the workspace-info providers
# (module-level wiring, same as the recap/compaction summarizers).
import omni_code.agents.omni.context  # noqa: F401


def _omni_spec():
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    project_yml = Path(__file__).parent.parent / "omni_code" / "project.yml"
    project = load_project(str(project_yml))
    return project, build_spec_from_project(project, "omni")


def _capability_server_functions(spec, cap_name):
    caps = {
        getattr(cap, "capability_name", None): cap
        for cap in getattr(spec, "resolved_capabilities", [])
    }
    assert cap_name in caps, f"omni agent does not activate {cap_name!r}"
    return {
        entry.get("name"): entry["func"]
        for entry in (getattr(caps[cap_name], "server_function_specs", None) or [])
    }


def test_workspace_info_pack_resolves_to_builtins():
    """The omni agent's ``workspace_info`` capability carries the fs_*
    picker functions from the framework builtin module — omni-code no
    longer ships its own filesystem server functions."""
    _, spec = _omni_spec()
    funcs = _capability_server_functions(spec, "workspace_info")

    for name in (
        "fs_get_cwd",
        "fs_get_workspace_root",
        "fs_get_home",
        "fs_list_dir",
        "fs_get_sources",
    ):
        assert name in funcs, f"{name} missing from workspace_info pack"
        assert funcs[name].__module__ == (
            "omniagents.builtin.server_functions.workspace_info"
        )

    # The explicit server_functions list no longer carries them.
    explicit = [
        entry.get("name") if isinstance(entry, dict) else entry
        for entry in (getattr(spec, "server_functions", None) or [])
    ]
    assert not any(str(name).startswith("fs_") for name in explicit)
    assert "session_ensure" not in explicit


def test_terminal_pack_resolves_to_builtins():
    """The omni agent's ``terminal`` capability carries terminal.create /
    terminal.close from the framework builtin module."""
    _, spec = _omni_spec()
    funcs = _capability_server_functions(spec, "terminal")

    for name in ("terminal.create", "terminal.close"):
        assert name in funcs, f"{name} missing from terminal pack"
        assert funcs[name].__module__ == (
            "omniagents.builtin.server_functions.terminal"
        )

    explicit = [
        entry.get("name") if isinstance(entry, dict) else entry
        for entry in (getattr(spec, "server_functions", None) or [])
    ]
    assert "terminal.create" not in explicit
    assert "terminal.close" not in explicit


def test_workspace_info_seams_read_published_serve_state():
    """The framework seams default to the state ``omni serve`` publishes
    into ``serve_state`` — omni-code installs no providers (the old lambda
    installs in the context module are gone)."""
    assert ws_seams.workspace_sources_provider() is None
    assert ws_seams.manifest_root_provider() is None
    assert ws_seams.sandbox_session_provider() is None

    sentinel_session = object()
    aws.set_manifest_root("/workspace/proj")
    aws.set_sources([{"kind": "local", "mountName": "proj", "path": "/host/proj"}])
    aws.set_sandbox_session(sentinel_session)
    try:
        assert ws_seams.get_manifest_root() == "/workspace/proj"
        assert ws_seams.get_workspace_sources() == [
            {"kind": "local", "mountName": "proj", "path": "/host/proj"}
        ]
        assert ws_seams.get_sandbox_session() is sentinel_session
    finally:
        aws.set_manifest_root(None)
        aws.set_sources(None)
        aws.set_sandbox_session(None)

    assert ws_seams.get_manifest_root() is None
    assert ws_seams.get_workspace_sources() == []
    assert ws_seams.get_sandbox_session() is None
