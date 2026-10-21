"""Verify pack selection against real product/framework capability resolution."""

import shutil
from pathlib import Path

import yaml
from omniagents.core.project.runtime import build_spec_from_project, load_project

ROOT = Path(__file__).resolve().parents[1] / "omni_code"


def names(spec):
    return {tool.name for cap in spec.resolved_capabilities for tool in cap.tools()}


def test_legacy_swarm_pack_resolves_to_workers(tmp_path):
    target = tmp_path / "omni_code"
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__"))
    baseline = build_spec_from_project(
        load_project(str(target / "project.yml")), agent_key="omni"
    )
    assert "spawn_worker" in names(baseline)
    assert "create_swarm" not in names(baseline)
    assert any(
        "## Worker Agents" in cap.instructions_text
        for cap in baseline.resolved_capabilities
    )
    path = target / "agents/omni/agent.yml"
    config = yaml.safe_load(path.read_text())
    config["capabilities"] = [
        "swarms" if name == "workers" else name for name in config["capabilities"]
    ]
    path.write_text(yaml.safe_dump(config))
    alternative = build_spec_from_project(
        load_project(str(target / "project.yml")), agent_key="omni"
    )
    assert "spawn_worker" in names(alternative)
    assert not {"create_swarm", "dispatch_swarm_member", "close_swarm"} & names(
        alternative
    )
    instructions = (
        alternative.get_agent_instructions()
        + "\n"
        + "\n".join(
            (c.instructions_text or "") for c in alternative.resolved_capabilities
        )
    )
    assert "peer_communication" in instructions
    worker = build_spec_from_project(
        load_project(str(target / "project.yml")), agent_key="worker"
    )
    assert not {"spawn_worker", "create_swarm"} & names(worker)


def test_resident_and_regular_sessions_use_same_workers():
    from types import SimpleNamespace

    from omniagents.core.capabilities.profiles import spec_for_capability_profile

    project = load_project(str(ROOT / "project.yml"))
    for key in ("omni", "voice_agent"):
        spec = build_spec_from_project(project, agent_key=key)
        resident = spec_for_capability_profile(
            spec, SimpleNamespace(variables={"capability_profile": "resident"})
        )
        assert names(resident) == names(spec)
        assert "spawn_worker" in names(resident)
        assert "spawn_worker" in names(spec)
        assert "create_swarm" not in names(spec)
    worker = build_spec_from_project(project, agent_key="worker")
    assert not worker.capability_profiles
