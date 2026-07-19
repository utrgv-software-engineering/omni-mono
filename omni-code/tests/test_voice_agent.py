from pathlib import Path

import yaml


def test_voice_agent_yaml_has_required_fields():
    """Test that voice_agent agent.yml has required configuration."""
    agent_yml = Path(__file__).parent.parent / "omni_code" / "agents" / "voice_agent" / "agent.yml"
    config = yaml.safe_load(agent_yml.read_text(encoding="utf-8"))

    assert config["name"] == "voice_agent"
    assert config["label"] == "Omni Code Voice"
    # Tools are activated via capability packs (capabilities migration);
    # the voice agent gets a focused subset — file/shell + web/search +
    # slash dispatch + markdown conversion.
    assert isinstance(config["capabilities"], list)
    assert set(config["capabilities"]) >= {"filesystem", "shell", "web", "markdown", "slash"}
    # No notebook/workers/tasks — voice latency favors a tighter tool surface.
    assert not {"notebook", "workers", "tasks"} & set(config["capabilities"])
    assert config["use_safe_agent"] is True


def test_voice_instructions_reference_voice_expectations():
    """Test that voice instructions contain voice-specific guidance."""
    instructions_md = Path(__file__).parent.parent / "omni_code" / "agents" / "voice_agent" / "instructions.md"
    text = instructions_md.read_text(encoding="utf-8")

    assert "Omni Code Voice" in text
    assert "Voice Expectations" in text
