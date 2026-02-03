import pytest

from omni_code import update_cli
from omni_code.updater import UpdateCheckResult


def test_update_cli_yes_skips_prompt_and_runs_upgrade(monkeypatch, capsys):
    monkeypatch.setattr(
        update_cli,
        "check_for_update",
        lambda **_: UpdateCheckResult(current_version="0.4.2", latest_version="0.4.3"),
    )

    called = {"upgrade": 0}

    monkeypatch.setattr(update_cli, "run_pipx_upgrade", lambda: called.__setitem__("upgrade", called["upgrade"] + 1))

    monkeypatch.setattr(update_cli, "_prompt_confirm", lambda _: (_ for _ in ()).throw(RuntimeError("prompted")))

    update_cli.main(["--yes"])

    out = capsys.readouterr().out
    assert "Update available: 0.4.2 -> 0.4.3" in out
    assert "Upgrade complete" in out
    assert called["upgrade"] == 1


def test_update_cli_check_exits_1_when_update_available(monkeypatch):
    monkeypatch.setattr(
        update_cli,
        "check_for_update",
        lambda **_: UpdateCheckResult(current_version="0.4.2", latest_version="0.4.3"),
    )

    with pytest.raises(SystemExit) as exc:
        update_cli.main(["--check"])

    assert exc.value.code == 1


def test_update_cli_exits_0_when_up_to_date(monkeypatch):
    monkeypatch.setattr(
        update_cli,
        "check_for_update",
        lambda **_: UpdateCheckResult(current_version="0.4.2", latest_version="0.4.2"),
    )

    with pytest.raises(SystemExit) as exc:
        update_cli.main([])

    assert exc.value.code == 0
