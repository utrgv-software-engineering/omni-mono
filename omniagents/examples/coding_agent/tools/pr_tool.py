import subprocess
from typing import Dict, Any

from omniagents.core import function_tool

from .file_tools import _validate_git_environment

@function_tool
def create_pull_request(title: str, body: str) -> Dict[str, Any]:
    """Push the current branch and create a draft PR via ``gh``.

    This tool assumes the ``gh`` CLI is configured for the repository.
    It pushes the active branch and then runs ``gh pr create --draft``
    with the provided title and body.
    """

    validation = _validate_git_environment()
    if not validation["valid"]:
        return {
            "error": f"CANNOT CREATE PR: {validation['error']}",
            "required_action": validation["required_action"],
        }

    branch = validation.get("branch")
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
                "--draft",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return {"success": True, "url": result.stdout.strip()}
        return {"success": False, "error": result.stderr.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}
