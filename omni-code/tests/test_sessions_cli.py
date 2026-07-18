import json

import pytest

from omni_code import sessions_cli
import omniagents.core.session.history_db as history_db
from omniagents.core.paths import get_sessions_db_path


def _reset_history_db(monkeypatch):
    monkeypatch.setattr(history_db, "_STORES", {})


def _seed_session(*, project: str, agent: str, session_id: str, messages: list[dict], archived: bool = False):
    history_db.register_session(session_id, project_slug=project, agent_slug=agent)
    for msg in messages:
        history_db.append_message(session_id, msg, project_slug=project, agent_slug=agent)
    if archived:
        history_db.set_archived(session_id, True, project_slug=project, agent_slug=agent)


def test_build_model_provider_uses_chat_completions_when_configured():
    from omni_code.utility_runs import build_model_provider

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

    sessions_cli.main(["--agent", agent, "list", "--all"])

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

    sessions_cli.main(["--agent", agent, "list", "--include-archived", "--all"])

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

    sessions_cli.main(["--agent", agent, "export", "s1", "--format", "jsonl"])

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

    sessions_cli.main(["--agent", agent, "search", "world", "--limit", "10"])

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

    with pytest.raises(SystemExit) as exc:
        sessions_cli.main(["--agent", agent, "export", "missing"])

    assert exc.value.code == 1


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

    monkeypatch.setattr("omni_code.models.load_models_config", lambda: {})
    monkeypatch.setattr(
        "omni_code.models.resolve_model_for_runtime",
        lambda *_args, **_kwargs: {"provider": "openai", "model": "gpt-4.1", "api_key": "x"},
    )

    captured = {}

    async def fake_run(*, model, runtime, prompt):
        captured["model"] = model
        captured["runtime"] = runtime
        captured["prompt"] = prompt
        return json.dumps({"summary": "ok", "decisions": [], "constraints": [], "key_files": [], "commands": [], "tests": [], "open_questions": [], "next_steps": [], "notable_errors": []})

    monkeypatch.setattr(sessions_cli, "_run_session_summarizer_agent", fake_run)

    sessions_cli.main(["--agent", agent, "summarize", "s1", "--tail", "10"])

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["session_id"] == "s1"
    assert data["summary"] == "ok"
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

    monkeypatch.setattr("omni_code.models.load_models_config", lambda: {})
    monkeypatch.setattr(
        "omni_code.models.resolve_model_for_runtime",
        lambda *_args, **_kwargs: {"provider": "openai", "model": "gpt-4.1", "api_key": "x"},
    )

    async def fake_run(*, model, runtime, prompt):
        return "not-json"

    monkeypatch.setattr(sessions_cli, "_run_session_summarizer_agent", fake_run)

    sessions_cli.main(["--agent", agent, "summarize", "s1"])

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["raw"] == "not-json"
    assert data["session_id"] == "s1"


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

    sessions_cli.main(["--agent", agent, "list", "--after", "2024-06-01", "--all"])

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    ids = {s["id"] for s in data}
    assert ids == {"s2"}
