import pytest
from unittest.mock import patch, MagicMock, call

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield

def test_create_git_worktree_basic(mock_function_tool):
    with patch('examples.coding_agent.tools.file_tools.subprocess.run') as mock_run, \
         patch('examples.coding_agent.tools.file_tools.os.chdir') as mock_chdir, \
         patch('examples.coding_agent.tools.file_tools.subprocess.check_output') as mock_check:
        mock_run.return_value = MagicMock()
        mock_check.return_value = 'abc123\n'
        from examples.coding_agent.tools.file_tools import create_git_worktree
        result = create_git_worktree('demo-task', base_branch='dev')

        assert result['success'] is True
        calls = [
            call(['git', 'fetch', 'origin'], check=True, cwd='.'),
            call(['git', 'switch', 'dev'], check=True, cwd='.'),
            call(['git', 'pull', '--ff-only'], check=True, cwd='.'),
        ]
        mock_run.assert_has_calls(calls, any_order=False)
        # The last call should be worktree add with dev branch
        assert any('worktree' in c.args[0] for c in mock_run.call_args_list)

