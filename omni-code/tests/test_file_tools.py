import os
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def mock_rich_tools(monkeypatch):
    """Patch rich_function_tool and RichToolOutput so functions are directly callable
    and we can easily inspect their returned metadata in tests.
    """
    # Identity decorator
    monkeypatch.setattr('omniagents.core.tools.rich_function_tool', lambda f: f, raising=False)
    # Identity decorator for approvals
    monkeypatch.setattr('omniagents.core.tools.approval_metadata', lambda *args, **kwargs: (lambda f: f), raising=False)

    # Minimal stub for RichToolOutput that exposes ui_metadata
    class DummyRTO:
        def __init__(self, llm_output, ui_metadata):
            self.llm_output = llm_output
            self.ui_metadata = ui_metadata

        # Convenience for tests: index into ui_metadata
        def __getitem__(self, key):
            return self.ui_metadata.get(key)

    monkeypatch.setattr('omniagents.core.tools.RichToolOutput', DummyRTO, raising=False)
    yield


@pytest.fixture
def tmp_workdir(tmp_path: Path) -> Path:
    """Create a temporary working directory with some files including hidden ones."""
    (tmp_path / 'a.py').write_text('print("hello")\n')
    (tmp_path / 'b.txt').write_text('hello world\nbye world\n')

    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'c.txt').write_text('subdir line 1\nsubdir line 2\n')

    # Hidden files
    (tmp_path / '.hidden.txt').write_text('secret line\n')
    (sub / '.hidden2.md').write_text('hidden content\n')
    return tmp_path


def test_glob_files_recursive_python(tmp_workdir: Path):
    from tools.file_tools import glob_files

    res = glob_files('**/*.py', path=str(tmp_workdir))
    files = res['value']

    assert any(p.endswith('a.py') for p in files)
    assert all(str(tmp_workdir) in p for p in files)


def test_glob_files_everything_includes_hidden_when_requested(tmp_workdir: Path):
    from tools.file_tools import glob_files

    res = glob_files('**/*', path=str(tmp_workdir), ignore_hidden=False, respect_gitignore=False)
    items = res['value']

    # Hidden files should be present due to the supplemental search
    assert any(p.endswith('.hidden.txt') for p in items)
    assert any(p.endswith('.hidden2.md') for p in items)


def test_glob_files_nonrecursive_does_not_cross_directories(tmp_workdir: Path):
    from tools.file_tools import glob_files

    res = glob_files('*.txt', path=str(tmp_workdir), ignore_hidden=False, respect_gitignore=False)
    items = res['value']

    # Should include top-level b.txt, but not sub/c.txt
    assert any(p.endswith('b.txt') for p in items)
    assert not any(p.endswith('sub/c.txt') for p in items)


def test_glob_files_subdir_pattern_matches_only_immediate_children(tmp_workdir: Path):
    from tools.file_tools import glob_files

    # sub/*.md should include .hidden2.md when ignore_hidden=False
    res = glob_files('sub/*.md', path=str(tmp_workdir), ignore_hidden=False, respect_gitignore=False)
    items = res['value']
    assert any(p.endswith('sub/.hidden2.md') for p in items)

    # And should exclude when ignore_hidden=True
    res2 = glob_files('sub/*.md', path=str(tmp_workdir), ignore_hidden=True, respect_gitignore=False)
    items2 = res2['value']
    assert not any(p.endswith('sub/.hidden2.md') for p in items2)


def test_grep_files_recursive_include_basename(tmp_workdir: Path):
    from tools.file_tools import grep_files

    # Should search recursively for *.txt files (grep-like include behavior)
    res = grep_files('hello', path=str(tmp_workdir), include='*.txt', respect_gitignore=False)
    results = res['value']
    files = [r['file'] for r in results]

    assert any(p.endswith('b.txt') for p in files)
    assert not any(p.endswith('c.txt') for p in files)  # c.txt has no 'hello'


def test_grep_files_include_none_includes_dotfiles_when_not_hidden(tmp_workdir: Path):
    from tools.file_tools import grep_files

    # ignore_hidden defaults to False in the tool; ensure hidden files are searched
    res = grep_files('secret', path=str(tmp_workdir), include=None, respect_gitignore=False)
    results = res['value']
    files = [r['file'] for r in results]

    assert any(p.endswith('.hidden.txt') for p in files)
