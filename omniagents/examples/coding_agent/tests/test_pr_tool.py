import pytest
from unittest.mock import patch, MagicMock, call

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


def test_create_pull_request(tmp_path, mock_function_tool, monkeypatch):
    repo = tmp_path
    (repo / '.git').mkdir(parents=True)
    monkeypatch.chdir(repo)

    with patch('examples.coding_agent.tools.pr_tool._validate_git_environment',
               return_value={'valid': True, 'branch': 'otto/test'}), \
         patch('examples.coding_agent.tools.pr_tool.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='https://example.com/pr/1', stderr='')
        from examples.coding_agent.tools.pr_tool import create_pull_request

        result = create_pull_request('My PR', 'Body text')

        assert result['success'] is True
        assert result['url'] == 'https://example.com/pr/1'
        mock_run.assert_has_calls([
            call(['git', 'push', '--set-upstream', 'origin', 'otto/test'], check=False),
            call(['gh', 'pr', 'create', '--title', 'My PR', '--body', 'Body text'], capture_output=True, text=True)
        ])
