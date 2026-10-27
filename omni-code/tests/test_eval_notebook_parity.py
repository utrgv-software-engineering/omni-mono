"""omni-code's ``evaluations/`` runs the same through the notebook as the CLI.

``EvalSuite.from_project`` loads the suite with the framework loader and
must hand the engine exactly the config ``eval suite run`` would. Two
host-only scenarios (one single-turn, one scripted multi-turn with a judge)
then run both ways, offline - every model request is answered by
``omniagents.testing.FakeModel`` - against the real omni agent and the
declared workspace environment, and must produce the same records.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from omniagents.core.eval.loader import load_eval_config
from omniagents.core.eval.pipeline import run_evaluation_async
from omniagents.notebook import EvalSuite
from omniagents.testing import install_fake_model

PROJECT = Path(__file__).resolve().parent.parent / "omni_code" / "project.yml"
SELECT = "name:simple_question_no_skill,name:preserves_explicit_constraint"


def test_from_project_hands_the_engine_the_cli_config():
    cfg = load_eval_config(str(PROJECT))
    suite = EvalSuite.from_project(PROJECT)
    assert len(suite.cases) == len(cfg.config["scenarios"]) > 0
    assert suite.to_config() == cfg.config


@pytest.fixture
def offline_home(tmp_path, monkeypatch):
    """Fake model for every request; caches, configs and history under tmp."""
    for var, sub in (
        ("HOME", "home"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "history.db"))
    monkeypatch.setenv("OMNIAGENTS_EVAL_TRANSCRIPTS_DIR", str(tmp_path / "t"))
    import omniagents.core.session.history_db as history_db

    importlib.reload(history_db)
    install_fake_model(monkeypatch, prefix=None)
    from omniagents.core.evaluation import registry

    monkeypatch.setattr(registry, "_MEASURES", dict(registry._MEASURES))
    yield tmp_path
    importlib.reload(history_db)


VOLATILE = {"session_id", "trace_group_id", "transcript_path", "duration_seconds"}


def _view(run):
    view = {k: v for k, v in run.items() if k not in VOLATILE | {"history"}}
    # The workspace environment stages each run in a fresh tempdir.
    view["metadata"] = {
        k: v
        for k, v in run["metadata"].items()
        if k not in ("environment_context", "group_id")
    }
    return view


def test_host_scenarios_run_identically(offline_home):
    cli = asyncio.run(
        run_evaluation_async(eval_config=load_eval_config(str(PROJECT), select=SELECT))
    )
    suite = EvalSuite.from_project(PROJECT, select=SELECT)
    nb = asyncio.run(suite.run(show_progress=False)).data

    assert sorted(r["scenario"] for r in cli["runs"]) == [
        "preserves_explicit_constraint",
        "simple_question_no_skill",
    ]
    assert all("error" not in r for r in cli["runs"]), cli["runs"]
    by_name = {r["scenario"]: r for r in cli["runs"]}
    # The project measures import and run (not just the harness).
    assert {
        m["name"]: m["passed"] for m in by_name["simple_question_no_skill"]["measures"]
    } == {
        "agents_md_in_instructions": True,
        "no_wrong_skill_activated": True,
    }
    assert by_name["preserves_explicit_constraint"]["exchanges"] == 2
    assert [_view(r) for r in nb["runs"]] == [_view(r) for r in cli["runs"]]
    assert nb["metrics"] == cli["metrics"]
    assert nb["summary"] == cli["summary"]
    assert nb["stats"] == cli["stats"]
    strip = lambda m: {k: v for k, v in m.items() if k != "created_at"}  # noqa: E731
    assert strip(nb["manifest"]) == strip(cli["manifest"])
    assert cli["manifest"]["select"] == SELECT and cli["manifest"]["max_turns"] == 100
