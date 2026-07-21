"""``omni update`` tests.

The command is mounted by the framework's product CLI host from the
``product.update:`` block in the packaged project.yml; these tests pin
that binding (program name, package name, exit codes) by dispatching
through the real entrypoint and faking the framework's check/upgrade
primitives.
"""

import pytest

import omniagents.core.updater as fw_updater
from omniagents.core.updater import UpdateCheckResult

import omni_code.governance as governance
from omni_code.cli import cli_main


@pytest.fixture(autouse=True)
def _no_governance(monkeypatch):
    monkeypatch.setattr(governance, "maybe_enforce_sandbox", lambda: None)


def test_update_cli_yes_skips_prompt_and_runs_upgrade(monkeypatch, capsys):
    monkeypatch.setattr(
        fw_updater,
        "check_for_update",
        lambda _config: UpdateCheckResult(
            current_version="0.4.2", latest_version="0.4.3"
        ),
    )

    called = {"upgrade": 0}
    monkeypatch.setattr(
        fw_updater,
        "run_pipx_upgrade",
        lambda config: called.__setitem__("upgrade", called["upgrade"] + 1),
    )
    monkeypatch.setattr(
        fw_updater,
        "_prompt_confirm",
        lambda _: (_ for _ in ()).throw(RuntimeError("prompted")),
    )

    assert cli_main(["update", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Update available: 0.4.2 -> 0.4.3" in out
    assert "Upgrade complete. Re-run `omni`" in out
    assert called["upgrade"] == 1


def test_update_cli_check_exits_1_when_update_available(monkeypatch):
    monkeypatch.setattr(
        fw_updater,
        "check_for_update",
        lambda _config: UpdateCheckResult(
            current_version="0.4.2", latest_version="0.4.3"
        ),
    )

    assert cli_main(["update", "--check"]) == 1


def test_update_cli_exits_0_when_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(
        fw_updater,
        "check_for_update",
        lambda _config: UpdateCheckResult(
            current_version="0.4.2", latest_version="0.4.2"
        ),
    )

    assert cli_main(["update"]) == 0
    assert "omni-code is up to date (0.4.2)." in capsys.readouterr().out
