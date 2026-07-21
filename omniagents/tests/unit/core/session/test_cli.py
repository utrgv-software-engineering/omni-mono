import argparse
import json

import pytest

from omniagents.core.session import history_db
from omniagents.core.session.cli import handle_sessions_command
from omniagents.core.session.summarize import (
    session_summarizer,
    set_session_summarizer,
)


@pytest.fixture(autouse=True)
def _reset_summarizer_seam():
    original = session_summarizer()
    yield
    set_session_summarizer(original)


def _summarize_args(session_id, **overrides):
    defaults = dict(
        command="sessions",
        sessions_cmd="summarize",
        session_id=session_id,
        project=None,
        agent=None,
        model=None,
        tail=200,
        pretty=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_handle_sessions_db_path(capsys):
    args = argparse.Namespace(
        command="sessions", sessions_cmd="db-path", project=None, agent=None
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert capsys.readouterr().out.strip().endswith("history.db")


def test_handle_sessions_list_json(capsys):
    history_db.register_session("session-1", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-1",
        {"role": "user", "content": "hello"},
        project_slug=None,
        agent_slug=None,
    )
    args = argparse.Namespace(
        command="sessions",
        sessions_cmd="list",
        project=None,
        agent=None,
        include_archived=True,
        after=None,
        before=None,
        offset=0,
        limit=20,
        all=False,
        stats=True,
        text=False,
        pretty=False,
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert "session-1" in capsys.readouterr().out


def test_handle_sessions_search_text(capsys):
    history_db.register_session("session-2", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-2",
        {"role": "user", "content": "search needle"},
        project_slug=None,
        agent_slug=None,
    )
    args = argparse.Namespace(
        command="sessions",
        sessions_cmd="search",
        query="needle",
        project=None,
        agent=None,
        role="user",
        include_archived=True,
        after=None,
        before=None,
        offset=0,
        limit=20,
        text=True,
        pretty=False,
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert "search needle" in capsys.readouterr().out


def test_handle_sessions_export_jsonl(capsys):
    history_db.register_session("session-3", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-3",
        {"role": "user", "content": "export me"},
        project_slug=None,
        agent_slug=None,
    )
    args = argparse.Namespace(
        command="sessions",
        sessions_cmd="export",
        session_id="session-3",
        project=None,
        agent=None,
        format="jsonl",
        pretty=False,
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert '"content":"export me"' in capsys.readouterr().out


def test_handle_sessions_summarize_with_fake_summarizer(capsys):
    history_db.register_session("session-4", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-4",
        {"role": "user", "content": "summarize needle"},
        project_slug=None,
        agent_slug=None,
    )
    captured = {}

    async def summarizer(prompt, *, model, project, agent, session_id):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["session_id"] = session_id
        return json.dumps({"summary": "did things", "decisions": ["a decision"]})

    set_session_summarizer(summarizer)

    code = handle_sessions_command(
        _summarize_args("session-4", model="some/model-ref")
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    # Summarizer output survives; identity + envelope defaults are ensured.
    assert out["summary"] == "did things"
    assert out["decisions"] == ["a decision"]
    assert out["session_id"] == "session-4"
    assert out["message_count"] == 1
    for key in ("constraints", "open_questions", "next_steps", "notable_errors"):
        assert out[key] == []
    # The seam saw the transcript prompt and the unresolved model ref.
    assert "summarize needle" in captured["prompt"]
    assert "Session metadata:" in captured["prompt"]
    assert captured["model"] == "some/model-ref"
    assert captured["session_id"] == "session-4"


def test_handle_sessions_summarize_custom_envelope(capsys):
    history_db.register_session("session-5", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-5",
        {"role": "user", "content": "hello"},
        project_slug=None,
        agent_slug=None,
    )

    async def summarizer(prompt, **kwargs):
        return "{}"

    set_session_summarizer(summarizer)
    args = _summarize_args("session-5")
    # Products pin their envelope keys on the namespace, like project/agent.
    args.summary_envelope = {"summary": "", "key_files": [], "tests": []}

    code = handle_sessions_command(args)

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["key_files"] == []
    assert out["tests"] == []
    assert "decisions" not in out


def test_handle_sessions_summarize_manifest_envelope(capsys, tmp_path):
    """Without args.summary_envelope, the CLI reads the installed
    product manifest's sessions.summary_envelope declaration; an
    explicit namespace envelope still wins."""
    from omniagents.core.product import bootstrap_product, reset_product_info

    manifest = tmp_path / "project.yml"
    manifest.write_text(
        "name: env_proj\n"
        "product:\n  slug: env_proj\n"
        "sessions:\n"
        "  summary_envelope:\n"
        '    summary: ""\n'
        "    key_files: []\n"
        "    tests: []\n",
        encoding="utf-8",
    )
    reset_product_info()
    try:
        bootstrap_product(manifest)

        history_db.register_session("session-6", project_slug=None, agent_slug=None)
        history_db.append_message(
            "session-6",
            {"role": "user", "content": "hello"},
            project_slug=None,
            agent_slug=None,
        )

        async def summarizer(prompt, **kwargs):
            return "{}"

        set_session_summarizer(summarizer)

        code = handle_sessions_command(_summarize_args("session-6"))
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["key_files"] == []
        assert out["tests"] == []
        assert "decisions" not in out

        # Explicit namespace envelope wins over the manifest declaration.
        args = _summarize_args("session-6")
        args.summary_envelope = {"summary": "", "verdicts": []}
        code = handle_sessions_command(args)
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["verdicts"] == []
        assert "key_files" not in out
    finally:
        reset_product_info()


def test_handle_sessions_summarize_missing_session(capsys):
    async def summarizer(prompt, **kwargs):
        return "{}"

    set_session_summarizer(summarizer)

    code = handle_sessions_command(_summarize_args("no-such-session"))

    assert code == 1
    assert "Session not found: no-such-session" in capsys.readouterr().out


def test_handle_sessions_summarize_without_summarizer(capsys):
    set_session_summarizer(None)

    code = handle_sessions_command(_summarize_args("session-4"))

    assert code == 1
    assert "set_session_summarizer" in capsys.readouterr().out
