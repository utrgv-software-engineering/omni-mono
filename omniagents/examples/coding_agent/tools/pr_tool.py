import subprocess
from typing import Dict, Any

from omniagents.core import function_tool

from .file_tools import _validate_git_environment, _get_worktree_metadata

@function_tool
def create_pull_request(title: str, body: str) -> Dict[str, Any]:
    """Push the current branch and create a pull request via ``gh``.

    This tool assumes the ``gh`` CLI is configured for the repository.
    It pushes the active branch and then runs ``gh pr create`` with the
    provided title and body, targeting the branch this worktree was created from.
    """

    validation = _validate_git_environment()
    if not validation["valid"]:
        return {
            "error": f"CANNOT CREATE PR: {validation['error']}",
            "required_action": validation["required_action"],
        }

    branch = validation.get("branch")
    
    # Get the base branch from worktree-specific storage
    base_branch = _get_worktree_metadata('base-branch')
    if not base_branch:
        return {
            "error": "CANNOT CREATE PR: No base branch found. Was this worktree created properly?",
            "required_action": "Run create_git_worktree first to set up the working environment"
        }
    
    try:
        if branch:
            subprocess.run([
                "git",
                "push",
                "--set-upstream",
                "origin",
                branch,
            ], check=False)

        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--base",
                base_branch,  # Target the original base branch
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return {
                "success": True, 
                "url": result.stdout.strip(),
                "base_branch": base_branch
            }
        return {"success": False, "error": result.stderr.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}