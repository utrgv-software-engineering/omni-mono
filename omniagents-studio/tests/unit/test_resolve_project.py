from pathlib import Path

from omniagents_studio.cli import resolve_project


def _manifest(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "project.yml"
    manifest.write_text(text)
    return manifest


def test_a_manifest_without_a_product_section_is_named_by_its_directory(tmp_path):
    manifest = _manifest(tmp_path / "support_desk", "name: anything\n")
    assert resolve_project(str(manifest)) == ("support_desk", manifest)
    # A directory holding the manifest resolves the same way.
    assert resolve_project(str(manifest.parent)) == ("support_desk", manifest)


def test_tracing_project_wins_then_the_product_slug(tmp_path):
    traced = _manifest(tmp_path / "a", "tracing:\n  project: traced\n")
    assert resolve_project(str(traced)) == ("traced", traced)
    product = _manifest(
        tmp_path / "b", "product:\n  name: Help Desk\n  slug: helpdesk\n"
    )
    assert resolve_project(str(product)) == ("helpdesk", product)


def test_a_bare_name_is_a_trace_project(tmp_path):
    assert resolve_project("demo") == ("demo", None)
