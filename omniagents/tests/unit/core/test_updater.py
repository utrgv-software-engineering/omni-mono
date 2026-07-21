import json

import pytest

from omniagents.core import updater
from omniagents.core.updater import UpdaterConfig


def make_config(tmp_path, **overrides):
    kwargs = dict(
        simple_index_url="https://pypi.fury.io/ericmichael/omni-code/",
        package_name="omni-code",
        cache_dir=tmp_path,
        env_prefix="OMNI_CODE",
    )
    kwargs.update(overrides)
    return UpdaterConfig(**kwargs)


def test_extract_versions_from_simple_index_parses_wheels_and_sdist():
    html = """
    <html>
      <a href='omni_code-0.4.2-py3-none-any.whl#sha256=abc'>wheel</a>
      <a href='omni_code-0.4.10-py3-none-any.whl#sha256=def'>wheel</a>
      <a href='omni-code-0.4.3.tar.gz#sha256=ghi'>sdist</a>
      <a href='unrelated-1.0.0-py3-none-any.whl'>nope</a>
    </html>
    """

    versions = updater._extract_versions_from_simple_index(html, "omni-code")

    assert sorted(versions) == ["0.4.10", "0.4.2", "0.4.3"]
    assert updater._pick_latest_version(versions) == "0.4.10"


def test_check_for_update_compares_current_and_latest(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "get_current_version", lambda _package: "0.4.2")

    class FakeResponse:
        status_code = 200
        text = "<a href='omni_code-0.4.3-py3-none-any.whl'>wheel</a>"

    monkeypatch.setattr(updater.requests, "get", lambda *_, **__: FakeResponse())

    result = updater.check_for_update(make_config(tmp_path))

    assert result.current_version == "0.4.2"
    assert result.latest_version == "0.4.3"
    assert result.update_available is True


def test_maybe_print_update_notice_uses_cache(monkeypatch, tmp_path, capsys):
    # cache_dir may be a zero-arg callable resolved at call time
    config = make_config(tmp_path, cache_dir=lambda: tmp_path, update_command_hint="omni update")
    monkeypatch.setattr(updater, "get_current_version", lambda _package: "0.4.2")
    monkeypatch.setattr(updater.time, "time", lambda: 1000.0)
    monkeypatch.setattr(updater, "fetch_latest_version", lambda **_: "0.4.3")

    updater.maybe_print_update_notice(config)
    out = capsys.readouterr().out
    assert "Update available (0.4.2 -> 0.4.3)." in out
    assert "Run: omni update" in out

    cache_path = updater.get_update_cache_path(config)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["latest_version"] == "0.4.3"

    monkeypatch.setattr(
        updater,
        "fetch_latest_version",
        lambda **_: (_ for _ in ()).throw(RuntimeError("network")),
    )
    monkeypatch.setattr(updater.time, "time", lambda: 1001.0)

    updater.maybe_print_update_notice(config)
    out2 = capsys.readouterr().out
    assert "Update available (0.4.2 -> 0.4.3)." in out2


def test_maybe_print_update_notice_respects_disable_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OMNI_CODE_UPDATE_CHECK", "0")
    monkeypatch.setattr(updater, "fetch_latest_version", lambda **_: "0.4.3")

    updater.maybe_print_update_notice(make_config(tmp_path))
    assert capsys.readouterr().out == ""


def test_run_pipx_upgrade_invokes_pipx(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(updater.shutil, "which", lambda _: "/usr/bin/pipx")

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        captured["check"] = check

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    updater.run_pipx_upgrade(make_config(tmp_path))

    assert captured["check"] is True
    assert captured["cmd"][:3] == ["pipx", "upgrade", "omni-code"]
    # extra index derived from simple_index_url by stripping the package segment
    assert "--extra-index-url https://pypi.fury.io/ericmichael/" in captured["cmd"]


def test_run_pipx_upgrade_errors_without_pipx(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="pipx is required"):
        updater.run_pipx_upgrade(make_config(tmp_path))
