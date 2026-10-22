"""One-name convention parity — literal construct-and-compare assertions.

The manifest declares only `product: {name, prog, update.index_url}`
plus the genuinely non-derivable blocks; everything below must resolve
to the exact historical values omni-code shipped with when each facet
was declared explicitly. If any of these literals drift, a derivation
convention regressed (or omni-code grew a deliberate identity change —
update the literals in that case).
"""

from pathlib import Path

from omniagents.core.product import load_product_info
from omniagents.core.project.runtime import load_project

import omni_code

PKG = Path(omni_code.__file__).resolve().parent
MANIFEST = PKG / "project.yml"


def test_product_identity_derives_to_historical_values():
    info = load_product_info(MANIFEST)
    assert info.name == "omni-code"
    assert info.prog == "omni"
    assert info.slug == "omni_code"
    assert info.label == "Omni Code"
    assert info.config_dir_name == "omni_code"
    assert info.windows_config_dir_name == "OmniCode"
    assert info.env_prefix == "OMNI_CODE"
    assert info.update is not None
    assert info.update.index_url == "https://pypi.fury.io/ericmichael/omni-code/"
    assert info.update.package == "omni-code"
    assert info.update.command_hint == "omni update"


def test_project_resolution_matches_historical_declarations():
    ctx = load_project(str(MANIFEST))
    # Entrypoint conventions: the agent named after prog, and voice_agent.
    assert ctx.entrypoint == "omni"
    assert ctx.voice_entrypoint == "voice_agent"
    # Tracing derives entirely from the product slug.
    assert ctx.tracing_defaults == {
        "provider": "none",
        "project": "omni_code",
        "name": "omni_code",
    }
    # The project identity is the product identity.
    assert ctx.product.slug == "omni_code"
    assert ctx.allow_user_mcp_servers is True
    # Paths: conventional package siblings, plus the two repo-level
    # overrides that stay declared.
    repo_root = PKG.parent
    assert ctx.paths["agents"] == [PKG / "agents"]
    assert ctx.paths["capabilities"] == [PKG / "capabilities"]
    assert ctx.paths["prompts"] == [PKG / "prompts"]
    assert ctx.paths["evaluations"] == [repo_root / "evaluations"]
    assert ctx.paths["target"] == [repo_root / "target"]
