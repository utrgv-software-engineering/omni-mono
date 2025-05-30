import pytest
from unittest.mock import patch, MagicMock, call
import os
import sys

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


def test_create_pull_request(mock_function_tool):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with patch('tools.pr_tool._validate_git_environment',
               return_value={'valid': True, 'branch': 'otto/test'}), \
         patch('tools.pr_tool.subprocess.run') as mock_run, \
         patch('tools.pr_tool._get_worktree_metadata') as mock_get_metadata:
        mock_get_metadata.return_value = 'main'
        mock_run.return_value = MagicMock(returncode=0, stdout='https://example.com/pr/1', stderr='')
        from tools.pr_tool import create_pull_request

        result = create_pull_request('My PR', 'Body text')

        assert result['success'] is True
        assert result['url'] == 'https://example.com/pr/1'
        
        # Verify the get metadata was called
        mock_get_metadata.assert_called_once_with('base-branch')
        
        # Verify the push and create PR commands
        mock_run.assert_has_calls([
            call(['git', 'push', '--set-upstream', 'origin', 'otto/test'], check=False),
            call(['gh', 'pr', 'create', '--title', 'My PR', '--body', 'Body text', '--base', 'main'], 
                 capture_output=True, text=True)
        ])
