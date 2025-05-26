import pytest
from unittest import mock
from unittest.mock import patch, MagicMock, call


# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    with patch("omniagents.core.function_tool", side_effect=lambda f: f):
        yield


def test_create_git_worktree_with_branch(mock_function_tool):
    with (
        patch("examples.coding_agent.tools.file_tools.subprocess.run") as mock_run,
        patch("examples.coding_agent.tools.file_tools.os.chdir") as mock_chdir,
        patch("examples.coding_agent.tools.file_tools.subprocess.check_output") as mock_check,
    ):
        mock_run.return_value = MagicMock()
        mock_check.return_value = "abc123\n"
        from examples.coding_agent.tools.file_tools import create_git_worktree

        result = create_git_worktree("demo-task", base_branch="dev")

        assert result["success"] is True
        mock_run.assert_has_calls(
            [
                call(
                    ["git", "worktree", "add", mock.ANY, "-b", mock.ANY, "dev"],
                    check=True,
                    cwd=".",
                ),
                call([
                    "git",
                    "config",
                    "otto.base-commit",
                    "abc123",
                ], check=True),
            ]
        )
        mock_check.assert_called_with(["git", "rev-parse", "dev"], text=True)


def test_create_git_worktree_default_branch(mock_function_tool):
    with (
        patch("examples.coding_agent.tools.file_tools.subprocess.run") as mock_run,
        patch("examples.coding_agent.tools.file_tools.os.chdir") as mock_chdir,
        patch("examples.coding_agent.tools.file_tools.subprocess.check_output") as mock_check,
    ):
        mock_run.return_value = MagicMock()
        mock_check.side_effect = ["dev\n", "abc123\n"]
        from examples.coding_agent.tools.file_tools import create_git_worktree

        result = create_git_worktree("demo-task")

        assert result["success"] is True
        mock_run.assert_has_calls(
            [
                call(
                    ["git", "worktree", "add", mock.ANY, "-b", mock.ANY, "dev"],
                    check=True,
                    cwd=".",
                ),
                call([
                    "git",
                    "config",
                    "otto.base-commit",
                    "abc123",
                ], check=True),
            ]
        )
        mock_check.assert_has_calls(
            [
                call(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True),
                call(["git", "rev-parse", "dev"], text=True),
            ]
        )
