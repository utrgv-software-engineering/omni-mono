"""Binding tests for omni-code's self-update flow.

The update mechanism (index scraping, version comparison, throttled
notice, pipx upgrade) lives in ``omniagents.core.updater`` and is tested
upstream. What omni-code owns — and what these tests cover — is the
``UpdaterConfig`` binding: the Gemfury index, the ``omni-code`` package
name, the config-dir cache, and the ``OMNI_CODE_*`` env knobs.
"""

import json

import omniagents.core.updater as fw_updater

from omni_code import updater


def test_updater_config_points_at_gemfury_omni_code():
    cfg = updater.UPDATER_CONFIG
    assert cfg.simple_index_url == "https://pypi.fury.io/ericmichael/omni-code/"
    assert cfg.simple_index_url == updater.GEMFURY_SIMPLE_INDEX_URL
    assert cfg.package_name == "omni-code"
    assert cfg.env_prefix == "OMNI_CODE"
    assert cfg.update_command_hint == "omni update"
    # pipx upgrade resolves the index root from the project page URL.
    assert cfg.resolved_extra_index_url == "https://pypi.fury.io/ericmichael/"


def test_update_cache_lives_in_omni_code_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from omni_code.config import get_config_dir

    assert (
        fw_updater.get_update_cache_path(updater.UPDATER_CONFIG)
        == get_config_dir() / "update.json"
    )


def test_maybe_print_update_notice_bound_to_omni_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(fw_updater, "get_current_version", lambda _pkg: "0.4.2")
    monkeypatch.setattr(fw_updater, "fetch_latest_version", lambda **_: "0.4.3")
    monkeypatch.setattr(fw_updater.time, "time", lambda: 1000.0)

    updater.maybe_print_update_notice()
    out = capsys.readouterr().out
    assert "Update available (0.4.2 -> 0.4.3)." in out
    # The notice advertises omni-code's own update command.
    assert "omni update" in out

    cache_path = fw_updater.get_update_cache_path(updater.UPDATER_CONFIG)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["latest_version"] == "0.4.3"


def test_maybe_print_update_notice_respects_omni_code_disable_env(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("OMNI_CODE_UPDATE_CHECK", "0")
    monkeypatch.setattr(fw_updater, "fetch_latest_version", lambda **_: "0.4.3")

    updater.maybe_print_update_notice()
    assert capsys.readouterr().out == ""


def test_run_pipx_upgrade_uses_gemfury_extra_index(monkeypatch):
    captured = {}

    monkeypatch.setattr(fw_updater.shutil, "which", lambda _: "/usr/bin/pipx")

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        captured["check"] = check

    monkeypatch.setattr(fw_updater.subprocess, "run", fake_run)

    updater.run_pipx_upgrade()

    assert captured["check"] is True
    assert captured["cmd"][:3] == ["pipx", "upgrade", "omni-code"]
    assert "--extra-index-url https://pypi.fury.io/ericmichael/" in captured["cmd"]
