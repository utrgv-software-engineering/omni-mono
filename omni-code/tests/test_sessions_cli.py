"""``omni sessions`` tests.

The command is fully conventional: the framework's reserved-sessions
default (`omniagents.product_cli.run_product_sessions_command`) pins the
`omni_code` project + entrypoint agent, the summarizer runs the
conventionally-named `session_summarizer` packaged agent, and the coding
summary envelope comes from project.yml (`sessions.summary_envelope`).
"""

import json

import pytest

import omni_code.governance as governance
from omni_code import PROJECT_MANIFEST
from omni_code.cli import cli_main
import omniagents.core.session.history_db as history_db
from omniagents.core.paths import get_sessions_db_path
from omniagents.core.session.summarize import manifest_session_envelope


@pytest.fixture(autouse=True)
def _no_governance(monkeypatch):
    monkeypatch.setattr(governance, "maybe_enforce_sandbox", lambda: None)


def _sessions(args: list[str]) -> int:
    return cli_main(["sessions", *args])


def _reset_history_db(monkeypatch):
    monkeypatch.setattr(history_db, "_STORES", {})


def _seed_session(*, project: str, agent: str, session_id: str, messages: list[dict], archived: bool = False):
    history_db.register_session(session_id, project_slug=project, agent_slug=agent)
    for msg in messages:
        history_db.append_message(session_id, msg, project_slug=project, agent_slug=agent)
    if archived:
        history_db.set_archived(session_id, True, project_slug=project, agent_slug=agent)


def test_build_model_provider_uses_chat_completions_when_configured():
    from omniagents.core.models import provider_from_runtime as build_model_provider

    provider, env = build_model_provider({
        "provider": "openai-compatible",
        "model": "gpt-test",
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
        "use_responses": False,
    })

    assert env == {}
    assert provider is not None
    assert provider._use_responses is False


def test_sessions_list_json_excludes_archived_by_default(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
        archived=False,
    )
    _seed_session(
        project=project,
        agent=agent,
        session_id="s2",
        messages=[{"role": "user", "content": "archived"}],
        archived=True,
    )

    assert _sessions(["--agent", agent, "list", "--all"]) == 0

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    ids = {s["id"] for s in data}
    assert ids == {"s1"}


def test_sessions_list_includes_archived(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
        archived=False,
    )
    _seed_session(
        project=project,
        agent=agent,
        session_id="s2",
        messages=[{"role": "user", "content": "archived"}],
        archived=True,
    )

    assert _sessions(["--agent", agent, "list", "--include-archived", "--all"]) == 0

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    ids = {s["id"] for s in data}
    assert ids == {"s1", "s2"}


def test_sessions_export_jsonl(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    )

    assert _sessions(["--agent", agent, "export", "s1", "--format", "jsonl"]) == 0

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    msgs = [json.loads(line) for line in out]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_sessions_search_finds_message(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello world"}],
    )
    _seed_session(
        project=project,
        agent=agent,
        session_id="s2",
        messages=[{"role": "assistant", "content": "nothing here"}],
    )

    assert _sessions(["--agent", agent, "search", "world", "--limit", "10"]) == 0

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert any(item["session_id"] == "s1" for item in data)


def test_sessions_export_missing_session_exits_1(monkeypatch, tmp_path):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert _sessions(["--agent", agent, "export", "missing"]) == 1


def _mock_model_store(monkeypatch, runtime):
    """Point omni-code's installed model store at a fixed runtime dict.

    The summarizer resolves through the framework's resolve_model_runtime,
    which reads the process-wide store — so the store instance (not the
    module-level alias) is what tests must patch.
    """
    from omniagents.core.models import model_store

    models = model_store()

    monkeypatch.setattr(
        models,
        "resolve_model_for_runtime",
        lambda model_name=None: runtime,
    )


def test_sessions_summarize_parses_json(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
    )

    _mock_model_store(
        monkeypatch, {"provider": "openai", "model": "gpt-4.1", "api_key": "x"}
    )

    captured = {}

    async def fake_run(agent_name, prompt, *, model=None, runtime=None, **kwargs):
        captured["agent_name"] = agent_name
        captured["model"] = model
        captured["runtime"] = runtime
        captured["prompt"] = prompt
        return json.dumps({"summary": "ok", "decisions": [], "constraints": [], "key_files": [], "commands": [], "tests": [], "open_questions": [], "next_steps": [], "notable_errors": []})

    monkeypatch.setattr(
        "omniagents.core.runtime.ephemeral.run_project_agent_once", fake_run
    )

    assert _sessions(["--agent", agent, "summarize", "s1", "--tail", "10"]) == 0

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["session_id"] == "s1"
    assert data["summary"] == "ok"
    assert captured["agent_name"] == "session_summarizer"
    assert captured["model"] == "gpt-4.1"
    assert "Transcript" in captured["prompt"]


def test_sessions_summarize_wraps_non_json(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
    )

    _mock_model_store(
        monkeypatch, {"provider": "openai", "model": "gpt-4.1", "api_key": "x"}
    )

    async def fake_run(agent_name, prompt, **kwargs):
        return "not-json"

    monkeypatch.setattr(
        "omniagents.core.runtime.ephemeral.run_project_agent_once", fake_run
    )

    assert _sessions(["--agent", agent, "summarize", "s1"]) == 0

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["raw"] == "not-json"
    assert data["session_id"] == "s1"
    # The coding envelope keys (declared in project.yml) are ensured
    # even on a raw wrap.
    envelope = manifest_session_envelope(PROJECT_MANIFEST)
    assert envelope is not None and "key_files" in envelope
    for key in envelope:
        assert key in data


def test_sessions_summarize_without_model_prints_hint_and_exits_1(
    monkeypatch, tmp_path, capsys
):
    """The no-model error fires from the installed summarizer — i.e.
    after the session lookup — and surfaces as a clean message, not a
    traceback."""
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
    )

    _mock_model_store(monkeypatch, {})

    assert _sessions(["--agent", agent, "summarize", "s1"]) == 1
    out = capsys.readouterr().out
    assert "No model configured. Run: omni model setup" in out


def test_sessions_summarize_missing_session_exits_1(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
    )

    _mock_model_store(
        monkeypatch, {"provider": "openai", "model": "gpt-4.1", "api_key": "x"}
    )

    assert _sessions(["--agent", agent, "summarize", "missing"]) == 1
    assert "Session not found: missing" in capsys.readouterr().out


def test_sessions_default_agent_is_entrypoint(monkeypatch, tmp_path, capsys):
    """Without --agent, the override pins the manifest's entrypoint agent."""
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    _seed_session(
        project="omni_code",
        agent="omni",
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert _sessions(["list", "--all"]) == 0
    data = json.loads(capsys.readouterr().out.strip())
    assert {s["id"] for s in data} == {"s1"}


def test_sessions_list_after_filters(monkeypatch, tmp_path, capsys):
    _reset_history_db(monkeypatch)
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)

    project = "omni_code"
    agent = "omni"

    _seed_session(
        project=project,
        agent=agent,
        session_id="s1",
        messages=[{"role": "user", "content": "one"}],
        archived=False,
    )
    _seed_session(
        project=project,
        agent=agent,
        session_id="s2",
        messages=[{"role": "user", "content": "two"}],
        archived=False,
    )

    db_path = get_sessions_db_path(project, agent)
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE sessions SET created_at=? WHERE session_id=?", ("2024-01-01 00:00:00", "s1"))
        conn.execute("UPDATE sessions SET created_at=? WHERE session_id=?", ("2025-01-01 00:00:00", "s2"))
        conn.commit()
    finally:
        conn.close()

    assert _sessions(["--agent", agent, "list", "--after", "2024-06-01", "--all"]) == 0

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    ids = {s["id"] for s in data}
    assert ids == {"s2"}
