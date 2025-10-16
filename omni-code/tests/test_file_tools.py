import asyncio
import json
import pytest
from pathlib import Path
from agents.tool import ToolContext
from agents.usage import Usage
from tools.file_tools import glob_files, grep_files


def invoke_tool(tool, workspace_root: Path, **payload):
    data = {k: v for k, v in payload.items() if v is not None}
    ctx = ToolContext(context={'workspace_root': str(workspace_root)}, usage=Usage(), tool_name=tool.name, tool_call_id='test-call')
    return asyncio.run(tool.on_invoke_tool(ctx, json.dumps(data)))


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
    try:
        (tmp_path / 'b_link.txt').symlink_to(tmp_path / 'b.txt')
    except OSError:
        pass
    return tmp_path


def test_glob_files_recursive_python(tmp_workdir: Path):
    res = invoke_tool(glob_files, tmp_workdir, pattern='**/*.py', path=str(tmp_workdir))
    files = res.ui_metadata['value']

    assert any(p.endswith('a.py') for p in files)
    assert all(str(tmp_workdir) in p for p in files)


def test_glob_files_everything_includes_hidden_when_requested(tmp_workdir: Path):
    res = invoke_tool(glob_files, tmp_workdir, pattern='**/*', path=str(tmp_workdir), ignore_hidden=False, respect_gitignore=False)
    items = res.ui_metadata['value']

    # Hidden files should be present due to the supplemental search
    assert any(p.endswith('.hidden.txt') for p in items)
    assert any(p.endswith('.hidden2.md') for p in items)


def test_glob_files_nonrecursive_does_not_cross_directories(tmp_workdir: Path):
    res = invoke_tool(glob_files, tmp_workdir, pattern='*.txt', path=str(tmp_workdir), ignore_hidden=False, respect_gitignore=False)
    items = res.ui_metadata['value']

    # Should include top-level b.txt, but not sub/c.txt
    assert any(p.endswith('b.txt') for p in items)
    assert not any(p.endswith('sub/c.txt') for p in items)


def test_glob_files_subdir_pattern_matches_only_immediate_children(tmp_workdir: Path):
    # sub/*.md should include .hidden2.md when ignore_hidden=False
    res = invoke_tool(glob_files, tmp_workdir, pattern='sub/*.md', path=str(tmp_workdir), ignore_hidden=False, respect_gitignore=False)
    items = res.ui_metadata['value']
    assert any(p.endswith('sub/.hidden2.md') for p in items)

    # And should exclude when ignore_hidden=True
    res2 = invoke_tool(glob_files, tmp_workdir, pattern='sub/*.md', path=str(tmp_workdir), ignore_hidden=True, respect_gitignore=False)
    items2 = res2.ui_metadata['value']
    assert not any(p.endswith('sub/.hidden2.md') for p in items2)


def test_glob_files_accepts_file_path(tmp_workdir: Path):
    target = tmp_workdir / 'b.txt'
    res = invoke_tool(glob_files, tmp_workdir, pattern='*.txt', path=str(target), ignore_hidden=False, respect_gitignore=False)
    items = res.ui_metadata['value']

    assert items == [str(target)]


def test_grep_files_recursive_include_basename(tmp_workdir: Path):
    # Should search recursively for *.txt files (grep-like include behavior)
    res = invoke_tool(grep_files, tmp_workdir, pattern='hello', path=str(tmp_workdir), include='*.txt', respect_gitignore=False)
    results = res.ui_metadata['value']
    files = [r['file'] for r in results]

    assert any(p.endswith('b.txt') for p in files)
    assert not any(p.endswith('c.txt') for p in files)  # c.txt has no 'hello'


def test_grep_files_include_none_includes_dotfiles_when_not_hidden(tmp_workdir: Path):
    # ignore_hidden defaults to False in the tool; ensure hidden files are searched
    res = invoke_tool(grep_files, tmp_workdir, pattern='secret', path=str(tmp_workdir), respect_gitignore=False)
    results = res.ui_metadata['value']
    files = [r['file'] for r in results]

    assert any(p.endswith('.hidden.txt') for p in files)


def test_grep_files_accepts_file_path(tmp_workdir: Path):
    target = tmp_workdir / 'b.txt'
    res = invoke_tool(grep_files, tmp_workdir, pattern='hello', path=str(target), respect_gitignore=False)
    results = res.ui_metadata['value']

    assert results[0]['file'] == str(target)
    assert results[0]['match_count'] == 1


def test_grep_files_deduplicates_symlinks(tmp_workdir: Path):
    link = tmp_workdir / 'b_link.txt'
    if not link.exists():
        pytest.skip('symlinks unsupported')

    res = invoke_tool(grep_files, tmp_workdir, pattern='hello', path=str(tmp_workdir), include='*.txt', respect_gitignore=False)
    results = res.ui_metadata['value']
    paths = [Path(entry['file']) for entry in results]
    real_paths = {p.resolve() for p in paths}

    assert len(paths) == len(real_paths)
    assert any(p.resolve() == (tmp_workdir / 'b.txt').resolve() for p in paths)
