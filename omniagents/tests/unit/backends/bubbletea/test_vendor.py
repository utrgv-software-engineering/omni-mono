import io
from pathlib import Path
import tarfile
import zipfile

import pytest

import omniagents.backends.bubbletea as bubbletea_module
from omniagents.backends.bubbletea import BubbleTeaBackend


@pytest.mark.parametrize("archive_name", ["omni-tui.tar.gz", "omni-tui.zip"])
def test_vendor_binary_extracts_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, archive_name: str
):
    backend = BubbleTeaBackend()
    target = "fake-target"
    vendor_root = tmp_path / "vendor"
    target_dir = vendor_root / target
    target_dir.mkdir(parents=True)
    binary_name = backend._binary_filename()
    data = b"payload"
    archive_path = target_dir / archive_name
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr(f"nested/{binary_name}", data)
    else:
        with tarfile.open(archive_path, "w:gz") as tf:
            file_info = tarfile.TarInfo(name=f"nested/{binary_name}")
            file_info.size = len(data)
            tf.addfile(file_info, io.BytesIO(data))
    monkeypatch.setattr(BubbleTeaBackend, "_target_triples", lambda self: [target])
    monkeypatch.setattr(BubbleTeaBackend, "_vendor_root", lambda self: vendor_root)
    monkeypatch.setattr(
        BubbleTeaBackend, "_vendor_cache_dir", lambda self: tmp_path / "cache"
    )
    result = backend._vendor_binary()
    assert result is not None
    assert result.exists()
    assert result.read_bytes() == data
    again = backend._vendor_binary()
    assert again == result


def test_vendor_binary_prefers_direct_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    backend = BubbleTeaBackend()
    target = "fake-target"
    vendor_root = tmp_path / "vendor"
    vendor_dir = vendor_root / target
    vendor_dir.mkdir(parents=True)
    binary_path = vendor_dir / backend._binary_filename()
    binary_path.write_text("payload")
    monkeypatch.setattr(BubbleTeaBackend, "_target_triples", lambda self: [target])
    monkeypatch.setattr(BubbleTeaBackend, "_vendor_root", lambda self: vendor_root)
    result = backend._vendor_binary()
    assert result == binary_path


def test_extract_vendor_archive_handles_digest_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    backend = BubbleTeaBackend()
    target = "fake-target"
    cache_root = tmp_path / "cache"
    archive_path = tmp_path / "omni-tui.tar.gz"
    data = b"payload"
    with tarfile.open(archive_path, "w:gz") as tf:
        file_info = tarfile.TarInfo(name=backend._binary_filename())
        file_info.size = len(data)
        tf.addfile(file_info, io.BytesIO(data))
    monkeypatch.setattr(BubbleTeaBackend, "_vendor_cache_dir", lambda self: cache_root)

    def boom(self, _):
        raise ValueError("boom")

    monkeypatch.setattr(BubbleTeaBackend, "_archive_digest", boom)
    result = backend._extract_vendor_archive(archive_path, target)
    assert result is None


def test_vendor_cache_dir_uses_home_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    backend = BubbleTeaBackend()
    monkeypatch.setattr(BubbleTeaBackend, "_user_cache_dir", lambda self: None)
    monkeypatch.setattr(bubbletea_module.Path, "home", lambda: tmp_path)
    path = backend._vendor_cache_dir()
    expected = tmp_path / ".omniagents" / "tui" / "vendor"
    assert path == expected
    assert path.exists()
