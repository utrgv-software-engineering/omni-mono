import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to simply return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


@pytest.fixture(autouse=True)
def mock_git_validation():
    with patch('examples.coding_agent.tools.file_tools._validate_git_environment', return_value={"valid": True}):
        yield


@pytest.fixture(autouse=True)
def reset_file_history():
    from examples.coding_agent.tools import file_tools
    file_tools._file_history.clear()
    yield
    file_tools._file_history.clear()


@pytest.fixture
def tmp_workdir(tmp_path):
    """Set up a temporary working directory populated with sample files."""
    # Create some files and folders
    (tmp_path / 'a.py').write_text('print("hello")\n')
    (tmp_path / 'b.txt').write_text('hello world\nbye world\n')
    subdir = tmp_path / 'sub'
    subdir.mkdir()
    (subdir / 'c.txt').write_text('subdir line 1\nsubdir line 2\n')
    return tmp_path


def test_glob_files_basic(mock_function_tool, tmp_workdir):
    """Ensure glob_files returns only *.py files when pattern is specified."""
    from examples.coding_agent.tools.file_tools import glob_files

    result = glob_files('*.py', path=str(tmp_workdir))

    assert result['count'] == 1
    assert result['matches'][0].endswith('a.py')


def test_grep_files_basic(mock_function_tool, tmp_workdir):
    """Ensure grep_files finds occurrences in matching files only."""
    from examples.coding_agent.tools.file_tools import grep_files

    # Search for the word 'hello' in *.txt files
    result = grep_files('hello', path=str(tmp_workdir), include='*.txt')

    assert result['count'] == 1
    assert any(match['file'].endswith('b.txt') for match in result['matches'])


def test_list_directory_nonrecursive(mock_function_tool, tmp_workdir):
    """Verify list_directory lists top-level items only when recursive=False."""
    from examples.coding_agent.tools.file_tools import list_directory

    listing = list_directory(path=str(tmp_workdir), recursive=False)

    # Should include a.py and b.txt but NOT files inside subdir
    files = listing['files']
    assert any(f.endswith('a.py') for f in files)
    assert any(f.endswith('b.txt') for f in files)
    assert not any(f.endswith('c.txt') for f in files)


def test_read_file_range(mock_function_tool, tmp_workdir):
    """Verify read_file reads the correct slice of lines."""
    from examples.coding_agent.tools.file_tools import read_file

    target_file = tmp_workdir / 'b.txt'
    rel = os.path.relpath(target_file, os.getcwd())
    # Must read entire file first
    read_file(file_path=rel, start_line=0, num_lines=None)
    result = read_file(file_path=rel, start_line=1, num_lines=1)

    assert result['line_count'] == 1
    assert result['lines'][0].strip() == 'bye world'


def test_edit_file_replace(mock_function_tool, tmp_workdir):
    """Ensure edit_file replaces text and reports number of replacements."""
    from examples.coding_agent.tools.file_tools import edit_file, read_file

    target_file = tmp_workdir / 'b.txt'
    rel = os.path.relpath(target_file, os.getcwd())
    read_file(file_path=rel, start_line=0, num_lines=None)
    replacements_result = edit_file(file_path=rel, old_text='world', new_text='planet')

    assert replacements_result['success'] is True
    assert replacements_result['replacements'] == 2

    # Confirm content changed
    after = read_file(file_path=rel, start_line=0, num_lines=None)
    assert 'planet' in after['content']
    assert 'world' not in after['content']


def test_write_file_append(mock_function_tool, tmp_path):
    """Test write_file in overwrite and append modes."""
    from examples.coding_agent.tools.file_tools import write_file, read_file

    target_file = tmp_path / 'log.txt'
    rel = os.path.relpath(target_file, os.getcwd())

    # Overwrite mode (create)
    write_file(file_path=rel, content='hello', append=False)
    # Append mode
    write_file(file_path=rel, content=' world', append=True)

    content = read_file(file_path=rel, start_line=0, num_lines=None)['content']
    assert content == 'hello world'


def test_read_partial_requires_full_read(mock_function_tool, tmp_workdir):
    from examples.coding_agent.tools import file_tools

    target_file = tmp_workdir / 'b.txt'
    rel = os.path.relpath(target_file, os.getcwd())

    result = file_tools.read_file(file_path=rel, start_line=1, num_lines=1)

    assert 'error' in result
    assert 'entire file' in result['error'].lower()


def test_edit_requires_full_read(mock_function_tool, tmp_workdir):
    from examples.coding_agent.tools import file_tools

    target_file = tmp_workdir / 'b.txt'
    rel = os.path.relpath(target_file, os.getcwd())

    result = file_tools.edit_file(file_path=rel, old_text='hello', new_text='hi')
    assert 'error' in result

    file_tools.read_file(file_path=rel, start_line=0, num_lines=None)
    result2 = file_tools.edit_file(file_path=rel, old_text='hello', new_text='hi')
    assert result2['success'] is True


def test_edit_requires_reread_after_edit(mock_function_tool, tmp_workdir):
    from examples.coding_agent.tools import file_tools

    target_file = tmp_workdir / 'b.txt'
    rel = os.path.relpath(target_file, os.getcwd())

    file_tools.read_file(file_path=rel, start_line=0, num_lines=None)
    first = file_tools.edit_file(file_path=rel, old_text='world', new_text='planet')
    assert first['success'] is True

    second = file_tools.edit_file(file_path=rel, old_text='planet', new_text='star')
    assert 'error' in second

    file_tools.read_file(file_path=rel, start_line=0, num_lines=None)
    third = file_tools.edit_file(file_path=rel, old_text='planet', new_text='star')
    assert third['success'] is True
