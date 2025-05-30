import pytest
import subprocess
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, call


@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to simply return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


@pytest.fixture(autouse=True) 
def mock_git_validation():
    """Mock git validation to allow tests to run without real git worktree."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with patch('tools.file_tools._validate_git_environment', return_value={"valid": True}):
        yield


def create_test_repo(tmp_path):
    """Create a test git repository with initial commit."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    os.chdir(repo_path)
    
    # Initialize git repo
    subprocess.run(['git', 'init'], check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], check=True)
    
    # Create initial file and commit
    (repo_path / 'initial.txt').write_text('Initial content\n')
    subprocess.run(['git', 'add', 'initial.txt'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], check=True)
    
    return repo_path


def test_diff_from_clean_branch(tmp_path, mock_function_tool):
    """Test diff computation starting from a clean branch with no uncommitted changes."""
    repo = create_test_repo(tmp_path)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree from clean state
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Make changes in the worktree
    (Path.cwd() / 'new_file.txt').write_text('New content\n')
    subprocess.run(['git', 'add', 'new_file.txt'], check=True)
    
    # Get diffs - should only show the new file
    diffs = _git_changed_diffs()
    assert len(diffs) == 1
    assert diffs[0][0] == 'new_file.txt'
    assert '+New content' in diffs[0][1]


def test_diff_with_uncommitted_changes(tmp_path, mock_function_tool):
    """Test diff computation starting from a branch with uncommitted changes."""
    repo = create_test_repo(tmp_path)
    
    # Create uncommitted changes before worktree creation
    (repo / 'existing.txt').write_text('Existing uncommitted content\n')
    subprocess.run(['git', 'add', 'existing.txt'], check=True)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree with uncommitted changes present
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Note: Git worktrees start with a clean working directory
    # They do NOT carry over uncommitted changes from the source branch
    # This is the expected behavior
    
    # Make new changes
    (Path.cwd() / 'agent_file.txt').write_text('Agent created this\n')
    subprocess.run(['git', 'add', 'agent_file.txt'], check=True)
    
    # Also create the same file that was uncommitted in the original branch
    # to test that it's treated as a new change
    (Path.cwd() / 'existing.txt').write_text('Agent version of existing.txt\n')
    subprocess.run(['git', 'add', 'existing.txt'], check=True)
    
    # Get diffs - should only show agent's changes
    diffs = _git_changed_diffs()
    file_names = [d[0] for d in diffs]
    
    assert 'agent_file.txt' in file_names
    assert 'existing.txt' in file_names  # This is the agent's version, not the pre-existing one
    
    # Verify that existing.txt shows as completely new (not as a modification)
    existing_diff = next(d[1] for d in diffs if d[0] == 'existing.txt')
    assert 'Agent version of existing.txt' in existing_diff
    assert 'Existing uncommitted content' not in existing_diff  # Original content should not appear


def test_diff_with_staged_changes(tmp_path, mock_function_tool):
    """Test diff computation starting from a branch with staged changes."""
    repo = create_test_repo(tmp_path)
    
    # Create and stage changes before worktree creation
    (repo / 'staged.txt').write_text('Staged content\n')
    subprocess.run(['git', 'add', 'staged.txt'], check=True)
    
    # Also create unstaged changes
    (repo / 'initial.txt').write_text('Initial content\nModified line\n')
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Make agent changes
    (Path.cwd() / 'agent_work.txt').write_text('Agent work\n')
    subprocess.run(['git', 'add', 'agent_work.txt'], check=True)
    
    # Get diffs
    diffs = _git_changed_diffs()
    file_names = [d[0] for d in diffs]
    
    assert 'agent_work.txt' in file_names
    assert 'staged.txt' not in file_names
    assert 'initial.txt' not in file_names


def test_diff_with_committed_then_modified(tmp_path, mock_function_tool):
    """Test diff computation when agent commits changes then modifies them again."""
    repo = create_test_repo(tmp_path)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Agent creates and commits a file
    (Path.cwd() / 'feature.py').write_text('def hello():\n    print("hello")\n')
    subprocess.run(['git', 'add', 'feature.py'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Add feature'], check=True)
    
    # Then modifies it again
    (Path.cwd() / 'feature.py').write_text('def hello():\n    print("hello world")\n')
    
    # Get diffs - should show all changes from initial state
    diffs = _git_changed_diffs()
    assert len(diffs) == 1
    assert diffs[0][0] == 'feature.py'
    # Should show the complete diff from nothing to current state
    assert '+def hello():' in diffs[0][1]
    assert '+    print("hello world")' in diffs[0][1]


def test_diff_with_deleted_files(tmp_path, mock_function_tool):
    """Test diff computation with deleted files."""
    repo = create_test_repo(tmp_path)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Delete the initial file
    subprocess.run(['git', 'rm', 'initial.txt'], check=True)
    
    # Create a new file
    (Path.cwd() / 'new.txt').write_text('New file\n')
    subprocess.run(['git', 'add', 'new.txt'], check=True)
    
    # Get diffs
    diffs = _git_changed_diffs()
    file_names = [d[0] for d in diffs]
    
    assert 'initial.txt' in file_names  # Deleted file should appear
    assert 'new.txt' in file_names
    
    # Check that deletion is shown in diff
    initial_diff = next(d[1] for d in diffs if d[0] == 'initial.txt')
    assert '-Initial content' in initial_diff


def test_diff_binary_files(tmp_path, mock_function_tool):
    """Test diff computation with binary files."""
    repo = create_test_repo(tmp_path)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Create a binary file
    (Path.cwd() / 'binary.bin').write_bytes(b'\x00\x01\x02\x03\xff\xfe\xfd')
    subprocess.run(['git', 'add', 'binary.bin'], check=True)
    
    # Get diffs
    diffs = _git_changed_diffs()
    assert len(diffs) == 1
    assert diffs[0][0] == 'binary.bin'
    assert '[Binary file changed]' in diffs[0][1]


def test_diff_mixed_changes(tmp_path, mock_function_tool):
    """Test complex scenario with multiple types of changes."""
    repo = create_test_repo(tmp_path)
    
    # Set up pre-existing state
    (repo / 'pre_existing.txt').write_text('Pre-existing\n')
    subprocess.run(['git', 'add', 'pre_existing.txt'], check=True)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.file_tools import create_git_worktree
    from main import _git_changed_diffs
    
    # Create worktree
    result = create_git_worktree("test-task")
    assert result["success"] is True
    
    # Agent makes various changes:
    # 1. Commits a new file
    (Path.cwd() / 'committed.txt').write_text('Committed by agent\n')
    subprocess.run(['git', 'add', 'committed.txt'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Agent commit'], check=True)
    
    # 2. Stages another file
    (Path.cwd() / 'staged.txt').write_text('Staged by agent\n')
    subprocess.run(['git', 'add', 'staged.txt'], check=True)
    
    # 3. Creates unstaged file
    (Path.cwd() / 'unstaged.txt').write_text('Unstaged by agent\n')
    
    # 4. Modifies the committed file
    (Path.cwd() / 'committed.txt').write_text('Committed by agent\nModified after commit\n')
    
    # Get diffs
    diffs = _git_changed_diffs()
    file_names = [d[0] for d in diffs]
    
    # Should include all agent changes
    assert 'committed.txt' in file_names
    assert 'staged.txt' in file_names
    assert 'unstaged.txt' in file_names
    
    # Should NOT include pre-existing changes
    assert 'pre_existing.txt' not in file_names
    
    # Verify committed.txt shows full current state
    committed_diff = next(d[1] for d in diffs if d[0] == 'committed.txt')
    assert '+Modified after commit' in committed_diff


def test_no_metadata_returns_empty_diffs(tmp_path, mock_function_tool):
    """Test that diff computation returns empty list when metadata is not available."""
    repo = create_test_repo(tmp_path)
    
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from main import _git_changed_diffs
    
    # Manually create a branch without going through create_git_worktree
    subprocess.run(['git', 'checkout', '-b', 'otto/test'], check=True)
    # No metadata is set
    
    # Make changes
    (repo / 'new.txt').write_text('New content\n')
    subprocess.run(['git', 'add', 'new.txt'], check=True)
    
    # Should return empty list since no worktree metadata exists
    diffs = _git_changed_diffs()
    assert len(diffs) == 0 