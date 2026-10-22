from pathlib import Path

from omniagents.backends.ink import InkBackend


def _tui(tmp_path: Path) -> Path:
    base = tmp_path / "tui"
    (base / "src").mkdir(parents=True)
    (base / "patches").mkdir()
    (base / "src" / "index.tsx").write_text("export {};\n")
    (base / "patches" / "ink+7.1.0.patch").write_text("diff a\n")
    (base / "package.json").write_text("{}\n")
    (base / "tsconfig.json").write_text("{}\n")
    return base


def test_a_changed_dependency_patch_invalidates_the_cached_build(tmp_path: Path):
    base = _tui(tmp_path)
    before = InkBackend()._compute_source_hash(base)
    (base / "patches" / "ink+7.1.0.patch").write_text("diff b\n")
    assert InkBackend()._compute_source_hash(base) != before


def test_the_hash_is_stable_for_unchanged_sources(tmp_path: Path):
    base = _tui(tmp_path)
    assert InkBackend()._compute_source_hash(base) == InkBackend()._compute_source_hash(
        base
    )
