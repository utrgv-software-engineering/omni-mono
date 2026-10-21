from pathlib import Path

import yaml


def test_voice_agent_yaml_has_required_fields():
    """Test that voice_agent agent.yml has required configuration."""
    agent_yml = (
        Path(__file__).parent.parent
        / "omni_code"
        / "agents"
        / "voice_agent"
        / "agent.yml"
    )
    config = yaml.safe_load(agent_yml.read_text(encoding="utf-8"))

    assert config["name"] == "voice_agent"
    assert config["label"] == "Omni Code Voice"
    # Tools are activated via capability packs. Voice reads and
    # delegates: read-only inspection + web/search +
    # markdown, with anything that writes or runs commands handed to a
    # worker (full filesystem/shell surface) or to `explore`.
    assert isinstance(config["capabilities"], list)
    caps = set(config["capabilities"])
    assert caps >= {
        "filesystem_readonly",
        "workers",
        "exploration",
    }
    # The mutating packs stay off voice — delegation IS the write path.
    # `web` is out for its `download_file` half, and `serpapi`/`markdown`
    # are out because their pack instructions name `web_fetch` /
    # `download_file`, which voice no longer has. `notebook`/`tasks` stay
    # off for latency (tighter tool surface).
    assert (
        not {
            "filesystem",
            "shell",
            "web",
            "serpapi",
            "markdown",
            "notebook",
            "tasks",
        }
        & caps
    )
    assert config["use_safe_agent"] is True


def test_voice_instructions_reference_voice_expectations():
    """Test that voice instructions contain voice-specific guidance."""
    instructions_md = (
        Path(__file__).parent.parent
        / "omni_code"
        / "agents"
        / "voice_agent"
        / "instructions.md"
    )
    text = instructions_md.read_text(encoding="utf-8")

    assert "Omni Code Voice" in text
    assert "Voice Expectations" in text


def test_voice_agent_has_no_mutating_tools():
    """The read-and-delegate invariant, pinned against the BUILT spec.

    The capability-list assertion above pins intent; this pins the
    resolved surface, which is what the model actually sees. Packs are
    expanded here the same way the realtime session expands them
    (rpc/realtime_session.py) — capability tools plus agent_tools —
    because voice builds its RealtimeAgent outside build_agent().
    """
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    root = Path(__file__).parent.parent
    proj = load_project(str(root / "omni_code" / "project.yml"))
    spec = build_spec_from_project(proj, agent_key="voice_agent")

    names = {t.name for cap in (spec.resolved_capabilities or []) for t in cap.tools()}
    names |= {c.tool_name for c in (spec.agent_tool_configs or []) if c.tool_name}

    assert not names & {
        "write_file",
        "edit_file",
        "apply_patch",
        "edit_notebook",
        "execute_bash",
        "bash_kill",
    }

    # Every read-only tool is approval-free: an approval card is the most
    # disruptive interruption in a spoken conversation. Only the tools
    # that change state outside the worker boundary stay gated.
    safe = set(spec.safe_agent_options.safe_tool_names or [])
    assert names - safe == {
        "spawn_worker",
        "send_worker_message",
        "close_worker",
        "resume_worker",
        "cleanup_worker_worktree",
    }
    # A typo'd safe-list entry silently gates the tool it meant to free.
    assert not safe - names
