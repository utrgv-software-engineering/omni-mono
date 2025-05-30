import pytest
import subprocess
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import tempfile


@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to simply return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


def test_git_changed_diffs_no_worktree():
    """Test that _git_changed_diffs returns empty list when not in a worktree."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    with patch('subprocess.check_output', side_effect=subprocess.CalledProcessError(1, 'git')):
        from main import _git_changed_diffs
        
        # Should return empty list instead of raising error
        result = _git_changed_diffs()
        assert result == []


@pytest.mark.asyncio
async def test_process_message_no_worktree():
    """Test that CodingAgentApp._process_message handles non-worktree gracefully."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    with patch('subprocess.check_output', side_effect=subprocess.CalledProcessError(1, 'git')):
        from main import CodingAgentApp
        from agents import Agent
        
        # Create a mock agent
        mock_agent = MagicMock(spec=Agent)
        mock_agent.run = AsyncMock(return_value="Test response")
        
        # Create app instance
        app = CodingAgentApp.from_agent(mock_agent)
        app.add_artifact = MagicMock()
        
        # Mock the parent class _process_message
        with patch.object(app.__class__.__bases__[0], '_process_message', new=AsyncMock()):
            # Process a message - should not fail even without worktree
            await app._process_message("Hello, world!")
            
            # Should not have added any artifacts
            app.add_artifact.assert_not_called()


@pytest.mark.asyncio 
async def test_process_message_with_worktree():
    """Test that CodingAgentApp._process_message generates diffs when in worktree."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # Mock subprocess to simulate being in a worktree
    def mock_subprocess_side_effect(*args, **kwargs):
        cmd = args[0]
        if "otto.base-commit" in cmd:
            return "abc123\n"
        elif "otto.initial-state" in cmd:
            return "abc123\n"
        elif "diff" in cmd and "--name-only" in cmd:
            return MagicMock(stdout="file1.txt\nfile2.py\n", returncode=0)
        elif "ls-files" in cmd:
            return MagicMock(stdout="", returncode=0)
        elif "cat-file" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        elif "--no-pager" in cmd and "diff" in cmd:
            return MagicMock(stdout="diff --git a/file1.txt b/file1.txt\n+new content", returncode=0)
        return MagicMock(stdout="", returncode=0)
    
    with patch('subprocess.check_output', side_effect=mock_subprocess_side_effect), \
         patch('subprocess.run', side_effect=mock_subprocess_side_effect), \
         patch('os.path.exists', return_value=True):
        from main import CodingAgentApp
        from agents import Agent
        
        # Create a mock agent
        mock_agent = MagicMock(spec=Agent)
        mock_agent.run = AsyncMock(return_value="Test response")
        
        # Create app instance
        app = CodingAgentApp.from_agent(mock_agent)
        app.add_artifact = MagicMock()
        
        # Mock the parent class _process_message
        with patch.object(app.__class__.__bases__[0], '_process_message', new=AsyncMock()):
            # Process a message - should generate diffs
            await app._process_message("Hello, world!")
            
            # Should have added artifacts for the diffs
            assert app.add_artifact.call_count > 0


def test_git_changed_diffs_error_handling():
    """Test that _git_changed_diffs handles various error conditions gracefully."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        
        # Initialize a git repo
        subprocess.run(['git', 'init'], check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'], check=True)
        
        # Set up mock Otto config
        subprocess.run(['git', 'config', 'otto.base-commit', 'HEAD'], check=True)
        subprocess.run(['git', 'config', 'otto.initial-state', 'HEAD'], check=True)
        
        from main import _git_changed_diffs
        
        # Should handle empty repo gracefully
        result = _git_changed_diffs()
        assert isinstance(result, list)
        assert len(result) == 0 