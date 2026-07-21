"""Binding tests for omni-code's self-update flow.

The update mechanism (index scraping, version comparison, throttled
notice, pipx upgrade) lives in ``omniagents.core.updater`` and is tested
upstream. What omni-code owns — and what these tests cover — is the
declaration: the ``product:`` section of the packaged project.yml
(installed process-wide by ``omni_code.identity``), from which
``UpdaterConfig.from_product()`` derives the Gemfury index, the
``omni-code`` package name, the config-dir cache, and the
``OMNI_CODE_*`` env knobs.
"""

import json

import omniagents.core.updater as fw_updater
from omniagents.core.product import load_product_info, product_info
from omniagents.core.updater import UpdaterConfig

from omni_code import identity


def _config() -> UpdaterConfig:
    return UpdaterConfig.from_product()


def test_product_identity_installed_from_packaged_project_yml():
    info = product_info()
    assert info == identity.PRODUCT
    assert info.name == "omni-code"
    assert info.prog == "omni"
    assert info.label == "Omni Code"
    assert info.slug == "omni_code"
    assert info.config_dir_name == "omni_code"
    assert info.windows_config_dir_name == "OmniCode"
    assert info.env_prefix == "OMNI_CODE"
    # The light parse of the packaged manifest agrees with what's installed.
    assert load_product_info(identity.PROJECT_MANIFEST) == info


def test_updater_config_from_product_points_at_gemfury_omni_code():
    cfg = _config()
    assert cfg.simple_index_url == "https://pypi.fury.io/ericmichael/omni-code/"
    assert cfg.package_name == "omni-code"
    assert cfg.env_prefix == "OMNI_CODE"
    assert cfg.update_command_hint == "omni update"
    # pipx upgrade resolves the index root from the project page URL.
    assert cfg.resolved_extra_index_url == "https://pypi.fury.io/ericmichael/"


def test_update_cache_lives_in_omni_code_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from omni_code.config import get_config_dir

    assert (
        fw_updater.get_update_cache_path(_config())
        == get_config_dir() / "update.json"
    )


def test_maybe_print_update_notice_bound_to_omni_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(fw_updater, "get_current_version", lambda _pkg: "0.4.2")
    monkeypatch.setattr(fw_updater, "fetch_latest_version", lambda **_: "0.4.3")
    monkeypatch.setattr(fw_updater.time, "time", lambda: 1000.0)

    fw_updater.maybe_print_update_notice(_config())
    out = capsys.readouterr().out
    assert "Update available (0.4.2 -> 0.4.3)." in out
    # The notice advertises omni-code's own update command.
    assert "omni update" in out

    cache_path = fw_updater.get_update_cache_path(_config())
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["latest_version"] == "0.4.3"


def test_maybe_print_update_notice_respects_omni_code_disable_env(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("OMNI_CODE_UPDATE_CHECK", "0")
    monkeypatch.setattr(fw_updater, "fetch_latest_version", lambda **_: "0.4.3")

    fw_updater.maybe_print_update_notice(_config())
    assert capsys.readouterr().out == ""


def test_run_pipx_upgrade_uses_gemfury_extra_index(monkeypatch):
    captured = {}

    monkeypatch.setattr(fw_updater.shutil, "which", lambda _: "/usr/bin/pipx")

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        captured["check"] = check

    monkeypatch.setattr(fw_updater.subprocess, "run", fake_run)

    fw_updater.run_pipx_upgrade(_config())

    assert captured["check"] is True
    assert captured["cmd"][:3] == ["pipx", "upgrade", "omni-code"]
    assert "--extra-index-url https://pypi.fury.io/ericmichael/" in captured["cmd"]
