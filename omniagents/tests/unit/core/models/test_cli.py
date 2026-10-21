"""Tests for the product-mountable model/provider CLI (non-interactive flows)."""

import pytest

from omniagents.core.models import ModelConfigStore
from omniagents.core.models.cli import run_model_command, run_provider_command


@pytest.fixture
def store(tmp_path):
    store = ModelConfigStore(config_dir=lambda: tmp_path)
    store.add_provider(
        "azure-prod",
        "azure",
        api_key="sk-x",
        base_url="https://x.example",
        api_version="2024-08-01-preview",
    )
    return store


def _run(store, argv, prog="myagent"):
    run_model_command(argv, store=store, prog=prog, product_label="MyAgent")


def test_add_list_show_remove_roundtrip(store, capsys):
    _run(
        store,
        [
            "add",
            "--name",
            "azure-prod/gpt-x",
            "--model",
            "deploy-x",
            "--label",
            "GPT X",
            "--default",
        ],
    )
    assert "Saved model 'azure-prod/gpt-x'" in capsys.readouterr().out

    _run(store, ["list"])
    out = capsys.readouterr().out
    assert "azure-prod/gpt-x" in out
    assert "(default)" in out

    _run(store, ["show", "azure-prod/gpt-x"])
    out = capsys.readouterr().out
    assert "Model ID: deploy-x" in out

    _run(store, ["remove", "azure-prod/gpt-x"])
    assert "Removed model" in capsys.readouterr().out


def test_usage_messages_render_product_prog(store, capsys):
    with pytest.raises(SystemExit):
        _run(store, ["remove"], prog="myagent")
    assert "Usage: myagent model remove" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        run_provider_command(["show"], store=store, prog="myagent")
    assert "Usage: myagent provider show" in capsys.readouterr().out


def test_add_requires_existing_provider(store, capsys):
    with pytest.raises(SystemExit):
        _run(store, ["add", "--name", "nope/m", "--model", "m"])
    out = capsys.readouterr().out
    assert "Provider 'nope' not found" in out
    assert "myagent provider add" in out


def test_list_json_is_machine_readable(store, capsys):
    import json

    _run(store, ["add", "--name", "azure-prod/gpt-x", "--model", "deploy-x"])
    capsys.readouterr()
    _run(store, ["list", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert any(m["name"] == "azure-prod/gpt-x" for m in payload["models"])


def test_provider_list_and_check(store, capsys):
    run_provider_command(["list"], store=store, prog="myagent")
    assert "azure-prod" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        _run(store, ["check"])
    # No default model configured -> non-zero.
    assert exc.value.code == 1
