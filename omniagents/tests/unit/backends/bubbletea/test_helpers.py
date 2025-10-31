from pathlib import Path

import pytest

import omniagents.backends.bubbletea as bubbletea_module
from omniagents.backends.bubbletea import BubbleTeaBackend


def test_build_binary_creates_output_and_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    backend = BubbleTeaBackend()
    commands = []

    monkeypatch.setattr(BubbleTeaBackend, "_user_cache_dir", lambda self: tmp_path)
    monkeypatch.setattr(BubbleTeaBackend, "_compute_source_hash", lambda self: "hash")
    monkeypatch.setattr(
        bubbletea_module.shutil,
        "which",
        lambda name: "/usr/bin/go" if name == "go" else None,
    )

    class Result:
        def __init__(self, code: int = 0):
            self.returncode = code

    def fake_run(cmd, cwd=None, check=False, env=None, stdout=None, stderr=None):
        commands.append(cmd)
        if cmd[0] == "go" and cmd[1] == "build":
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("binary")
            return Result(0)
        return Result(0)

    monkeypatch.setattr(bubbletea_module.subprocess, "run", fake_run)

    result = backend._build_binary(debug=True)

    assert result.exists()
    assert result.read_text() == "binary"
    assert commands[0][:2] == ["go", "mod"]
    assert commands[1][:2] == ["go", "build"]
    stamp = tmp_path / "omni-tui.hash"
    assert stamp.read_text(encoding="utf-8").strip() == "hash"


def test_build_binary_requires_go(monkeypatch: pytest.MonkeyPatch):
    backend = BubbleTeaBackend()
    monkeypatch.setattr(bubbletea_module.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        backend._build_binary()


def test_compute_source_hash_returns_digest():
    backend = BubbleTeaBackend()
    digest = backend._compute_source_hash()
    assert digest is None or len(digest) == 64


def test_cached_hash_roundtrip(tmp_path: Path):
    backend = BubbleTeaBackend()
    backend._write_cached_hash(tmp_path, "value")
    assert backend._read_cached_hash(tmp_path) == "value"


def test_find_binary_prefers_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    backend = BubbleTeaBackend()
    binary_path = tmp_path / backend._binary_filename()
    binary_path.write_text("binary")
    monkeypatch.setattr(BubbleTeaBackend, "_vendor_binary", lambda self: None)
    monkeypatch.setattr(BubbleTeaBackend, "_user_cache_dir", lambda self: tmp_path)
    result = backend._find_binary()
    assert result == binary_path


def test_find_binary_uses_system_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    backend = BubbleTeaBackend()
    system_path = tmp_path / backend._binary_filename()
    system_path.write_text("binary")
    monkeypatch.setattr(BubbleTeaBackend, "_vendor_binary", lambda self: None)
    monkeypatch.setattr(BubbleTeaBackend, "_user_cache_dir", lambda self: None)
    monkeypatch.setattr(bubbletea_module.shutil, "which", lambda name: str(system_path))
    result = backend._find_binary()
    assert result == system_path
