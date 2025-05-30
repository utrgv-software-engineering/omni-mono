import pytest
import subprocess
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to simply return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


def test_concurrent_worktrees_metadata_isolation(tmp_path, mock_function_tool):
    """Test that multiple worktrees maintain isolated metadata."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # Create a test repository
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    os.chdir(repo_path)
    
    # Initialize git repo
    subprocess.run(['git', 'init'], check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], check=True)
    
    # Create initial commit
    (repo_path / 'README.md').write_text('# Test Repo\n')
    subprocess.run(['git', 'add', 'README.md'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], check=True)
    
    # Get the current branch name (could be master or main)
    current_branch = subprocess.check_output(
        ['git', 'branch', '--show-current'],
        text=True
    ).strip()
    
    # Import after setting up the path
    from tools.file_tools import create_git_worktree, _get_worktree_metadata, _store_worktree_metadata
    
    # Create first worktree
    result1 = create_git_worktree("task-one", base_branch=current_branch)
    assert result1["success"] is True
    worktree1_path = os.getcwd()
    
    # Store some metadata in worktree 1
    assert _store_worktree_metadata('custom-key', 'value-from-worktree-1')
    
    # Verify we can retrieve it
    assert _get_worktree_metadata('custom-key') == 'value-from-worktree-1'
    assert _get_worktree_metadata('base-branch') == current_branch
    
    # Go back to the main repo
    os.chdir(repo_path)
    
    # Create second worktree
    result2 = create_git_worktree("task-two", base_branch=current_branch)
    assert result2["success"] is True
    worktree2_path = os.getcwd()
    
    # Store different metadata in worktree 2
    assert _store_worktree_metadata('custom-key', 'value-from-worktree-2')
    
    # Verify worktree 2 has its own metadata
    assert _get_worktree_metadata('custom-key') == 'value-from-worktree-2'
    assert _get_worktree_metadata('base-branch') == current_branch
    
    # Go back to worktree 1 and verify its metadata is unchanged
    os.chdir(worktree1_path)
    assert _get_worktree_metadata('custom-key') == 'value-from-worktree-1'
    
    # Clean up
    os.chdir(repo_path)
    subprocess.run(['git', 'worktree', 'remove', '--force', worktree1_path], check=True)
    subprocess.run(['git', 'worktree', 'remove', '--force', worktree2_path], check=True)


def test_metadata_not_in_working_directory(tmp_path, mock_function_tool):
    """Test that metadata is not stored in the working directory."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # Create a test repository
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    os.chdir(repo_path)
    
    # Initialize git repo
    subprocess.run(['git', 'init'], check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], check=True)
    
    # Create initial commit
    (repo_path / 'README.md').write_text('# Test Repo\n')
    subprocess.run(['git', 'add', 'README.md'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], check=True)
    
    # Get the current branch name (could be master or main)
    current_branch = subprocess.check_output(
        ['git', 'branch', '--show-current'],
        text=True
    ).strip()
    
    from tools.file_tools import create_git_worktree, _store_worktree_metadata
    
    # Create worktree
    result = create_git_worktree("test-task", base_branch=current_branch)
    assert result["success"] is True
    worktree_path = os.getcwd()
    
    # Get list of files before storing metadata
    files_before = set(os.listdir(worktree_path))
    
    # Store metadata
    assert _store_worktree_metadata('test-key', 'test-value')
    
    # Get list of files after storing metadata
    files_after = set(os.listdir(worktree_path))
    
    # Verify no new files were created in the working directory
    assert files_before == files_after
    
    # Verify git status is clean
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        capture_output=True,
        text=True
    )
    assert status_result.stdout.strip() == "" 