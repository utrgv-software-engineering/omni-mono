import pytest
from unittest import mock
from unittest.mock import patch, MagicMock, call
import os
import sys


# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    with patch("omniagents.core.function_tool", side_effect=lambda f: f):
        yield


def test_create_git_worktree_with_branch(mock_function_tool):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with (
        patch("tools.file_tools.subprocess.run") as mock_run,
        patch("tools.file_tools.os.chdir") as mock_chdir,
        patch("tools.file_tools.subprocess.check_output") as mock_check,
        patch("tools.file_tools._store_worktree_metadata") as mock_store_metadata,
    ):
        # Mock the status check to show clean working directory
        status_mock = MagicMock()
        status_mock.stdout = ""
        mock_run.side_effect = [
            MagicMock(),  # git worktree add
            status_mock,  # git status --porcelain
        ]
        mock_check.return_value = "abc123\n"
        # Mock metadata storage to always succeed
        mock_store_metadata.return_value = True
        
        from tools.file_tools import create_git_worktree

        result = create_git_worktree("demo-task", base_branch="dev")

        assert result["success"] is True
        # Verify the important calls were made
        assert any("worktree" in str(call) and "add" in str(call) for call in mock_run.call_args_list)
        # Verify metadata storage was called
        assert mock_store_metadata.call_count >= 3  # base-commit, base-branch, initial-state
        assert any(call[0][0] == 'base-commit' for call in mock_store_metadata.call_args_list)
        assert any(call[0][0] == 'base-branch' for call in mock_store_metadata.call_args_list)
        assert any(call[0][0] == 'initial-state' for call in mock_store_metadata.call_args_list)


def test_create_git_worktree_default_branch(mock_function_tool):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with (
        patch("tools.file_tools.subprocess.run") as mock_run,
        patch("tools.file_tools.os.chdir") as mock_chdir,
        patch("tools.file_tools.subprocess.check_output") as mock_check,
        patch("tools.file_tools._store_worktree_metadata") as mock_store_metadata,
    ):
        # Mock the status check to show clean working directory
        status_mock = MagicMock()
        status_mock.stdout = ""
        mock_run.side_effect = [
            MagicMock(),  # git worktree add
            status_mock,  # git status --porcelain
        ]
        mock_check.side_effect = ["dev\n", "abc123\n"]
        # Mock metadata storage to always succeed
        mock_store_metadata.return_value = True
        
        from tools.file_tools import create_git_worktree

        result = create_git_worktree("demo-task")

        assert result["success"] is True
        # Verify the git commands were called in the right order
        assert mock_check.call_count == 2  # HEAD and rev-parse
        assert any("worktree" in str(call) and "add" in str(call) for call in mock_run.call_args_list)
        # Verify metadata storage was called
        assert mock_store_metadata.call_count >= 3  # base-commit, base-branch, initial-state
        assert any(call[0][0] == 'base-commit' for call in mock_store_metadata.call_args_list)
        assert any(call[0][0] == 'base-branch' for call in mock_store_metadata.call_args_list)
        assert any(call[0][0] == 'initial-state' for call in mock_store_metadata.call_args_list)
