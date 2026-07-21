"""``omni update`` CLI tests.

The command is the framework's ``run_update_command`` with the config
defaulted from the product identity in the packaged project.yml; these
tests pin that binding (program name, package name, exit codes) by
faking the framework's check/upgrade primitives.
"""

import pytest

import omniagents.core.updater as fw_updater
from omniagents.core.updater import UpdateCheckResult

from omni_code import update_cli


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

    with pytest.raises(SystemExit) as exc:
        update_cli.main(["--yes"])

    assert exc.value.code == 0
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

    with pytest.raises(SystemExit) as exc:
        update_cli.main(["--check"])

    assert exc.value.code == 1


def test_update_cli_exits_0_when_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(
        fw_updater,
        "check_for_update",
        lambda _config: UpdateCheckResult(
            current_version="0.4.2", latest_version="0.4.2"
        ),
    )

    with pytest.raises(SystemExit) as exc:
        update_cli.main([])

    assert exc.value.code == 0
    assert "omni-code is up to date (0.4.2)." in capsys.readouterr().out
