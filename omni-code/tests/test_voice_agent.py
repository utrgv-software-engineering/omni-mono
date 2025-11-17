from omniagents.core.agents.safe import SafeAgent

from oagents import voice_agent


def test_voice_instructions_reference_voice_expectations(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    text = voice_agent.get_instructions()
    assert "Omni Code Voice" in text
    assert "Voice Expectations" in text


def test_create_voice_agent_returns_safe_agent():
    agent = voice_agent.create_voice_agent()
    assert isinstance(agent, SafeAgent)
    assert agent.name == "Omni Code Voice"
