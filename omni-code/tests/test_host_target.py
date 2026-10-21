"""Tests for the ``host`` execution target.

Covers:
* ``_is_host_profile`` predicate.
* The built-in default profile resolving to the host (no-sandbox) target.
* Coding instructions reflecting the routed workspace root.

Terminal backend selection is framework-owned and covered alongside the
routed ``terminal.*`` server functions in OmniAgents.
"""

from __future__ import annotations

from pathlib import Path

from omniagents.core.runtime import serve_harness as serve_cli

# Product identity resolves via the framework's entry-point discovery.
from omniagents.core.sandbox.profiles import (
    resolve_product_profile as resolve_profile,
)

# --------------------------------------------------------------------------
# Predicate + default profile
# --------------------------------------------------------------------------


def test_is_host_profile():
    assert serve_cli._is_host_profile({"client": {"type": "host"}}) is True
    assert serve_cli._is_host_profile({"client": {"type": "unix_local"}}) is False
    assert serve_cli._is_host_profile({"client": {"type": "docker"}}) is False
    assert serve_cli._is_host_profile({}) is False


def test_default_profile_is_the_host_target(tmp_path: Path):
    prof = resolve_profile(workspace_dir=tmp_path, project_id=None, explicit_path=None)
    assert (prof.raw.get("client") or {}).get("type") == "host"


def test_default_profile_declares_no_confinement_knob(tmp_path: Path):
    """No "host but fenced" tier — see the profile's own comment.

    This replaces an assertion that the profile declared ``confine: false``.
    That assertion passed for months while nothing read the key: it tested
    the declaration, not the effect, so the dead knob was invisible. The
    effect — the host target building an unconfined workspace — is framework
    behavior and is tested there
    (``tests/unit/core/runtime/test_environment_provisioner.py``).
    """
    prof = resolve_profile(workspace_dir=tmp_path, project_id=None, explicit_path=None)
    assert "confine" not in prof.raw


# --------------------------------------------------------------------------
# Instruction workspace path reflects the authoritative workspace_root
# --------------------------------------------------------------------------
#
# The workspace_root that reaches build_coding_context is guaranteed correct
# at the source: the Run's captured Workspace and EnvironmentLease select the
# sandbox manifest root. build_coding_context therefore just reflects whatever
# it is given — no manifest lookup and no host-derived fallback.


def test_instruction_workspace_reflects_given_workspace_root():
    """The [Workspace] line shows the workspace_root it is handed — which, for
    a sandbox run, is the container manifest root forced by the server."""
    from omni_code.agents.omni.context import format_agents_md_block

    block = format_agents_md_block({"workspace_root": "/workspace/myproject"})
    assert "[Workspace]\n/workspace/myproject" in block

    block = format_agents_md_block({"workspace_root": "/home/user/proj"})
    assert "[Workspace]\n/home/user/proj" in block
