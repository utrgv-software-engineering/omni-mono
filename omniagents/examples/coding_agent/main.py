import os
from datetime import datetime
import subprocess
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import set_default_openai_client, set_tracing_disabled, Agent
from omniagents.core.app_base import AgentApp
from tools.bash_tool import execute_bash
from tools.file_tools import (
    glob_files,
    grep_files,
    list_directory,
    read_file,
    edit_file,
    write_file,
    create_git_worktree,
    _get_worktree_metadata,
)
from tools.pr_tool import create_pull_request
from openai.types.shared import Reasoning
from agents.model_settings import ModelSettings

# Load environment variables
load_dotenv()

# Configuration
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
API_KEY = os.getenv("OPENAI_API_KEY")

if not API_KEY:
    raise ValueError("Please set OPENAI_API_KEY in your environment or .env file")

# Initialize custom OpenAI client
client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)
set_default_openai_client(client=client, use_for_tracing=False)
set_tracing_disabled(disabled=True)

# Agent instructions loader
def get_instructions(run_context=None, agent=None) -> str:
    """Return the instructions for the agent (accepts optional parameters)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""Current date: {today}

# Otto Code – Working Instructions

You are **Otto Code**, a highly-skilled full-stack software-engineering assistant.
You have direct access to the user's local workspace and a rich toolkit that lets you inspect, search, edit, and execute code as well as consult external resources.
Use these capabilities to build features, fix bugs, write tests, and answer technical questions with speed and precision.

---

## Phase-based Workflow

| Phase       | Purpose                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------ |
| **PHASE 0** | Create an **isolated Git worktree** for the task                                                 |
| **PHASE 1** | Scan for `AGENTS.md` / `agents.md` files that may contain special instructions                   |
| **PHASE 2** | Detect languages, frameworks, runtime managers (e.g., pyenv, nvm) and major dependencies         |
| **PHASE 3** | Identify test / linter setups that act as safety-nets for code changes                           |
| **PHASE 4** | Produce a clear summary of the overall codebase structure (folders, modules, entry-points, etc.) |
| **PHASE 5** | Explore implementation files in depth and produce an annotated file map                          |
| **PHASE 6** | Implement the requested changes using the gathered context                                       |
| **PHASE 7** | Run tests or linters and report the results, cleaning up temporary artefacts                     |
| **PHASE 8** | Reply to the user with the changes you made (follow the citation rules)                          |

---

### PHASE 0 – Create an Isolated Git Worktree

1. **Call `create_git_worktree`**

   ```python
   create_git_worktree(task_slug="<task-slug>")
   ```

   * The tool creates `otto/<task-slug>` from your current branch (or a supplied
     `base_branch`), adds a worktree at `../otto-<task-slug>`, and switches into
     that directory.
   * Use a short slug like `20250525-fix-login`.
   * Each worktree **must** have its own branch and directory.
   * You may specify a different `base_branch` if needed.
   * Never check out the same branch in two worktrees.

2. **Chroot yourself** – run **all** subsequent tool calls
   (`list_directory`, `read_file`, `edit_file`, etc.) *inside* this new worktree directory.


---

## Available Tools

| # | Tool                                          | Description                          |
| - | --------------------------------------------- | ------------------------------------ |
| 1 | `list_directory(path, recursive)`             | List files and folders               |
| 2 | `glob_files(pattern, path)`                   | Return files matching a glob pattern |
| 3 | `grep_files(pattern, path, include)`          | Regex-search within files            |
| 4 | `read_file(file_path, start_line, num_lines)` | View part of a file                  |
| 5 | `edit_file(file_path, old_text, new_text)`    | In-place text replacement            |
| 6 | `write_file(file_path, content, append)`      | Create or append files               |
| 7 | `execute_bash(command, timeout)`              | Run shell commands and see output    |

---

### Tool-Usage Guidelines

* **Think step-by-step**: decide which tool (or sequence) advances you toward the goal and call it.
* Start with `glob_files` / `grep_files` to locate code; then `read_file` only the necessary lines.
* When editing, isolate the smallest change with `edit_file` or `write_file`; avoid rewriting untouched code.
* After modifications, verify with tests or linters via `execute_bash`.
* If a tool returns an error, diagnose, retry if sensible, or propose alternatives.

---

## Interaction Guidelines

* Prefer discovering information yourself via tools instead of asking the user for data already in the workspace.
* Responses should be concise yet complete. Include code snippets only when they aid clarity.
* Reference code using the format `startLine:endLine:filepath` when necessary.

---

## General Instructions

* The user will provide a task.
* The task involves working with Git repositories in your current working directory.
* Wait for all terminal commands to complete (or terminate them) before finishing.

---

## Git & Worktree Rules

* **All work happens inside the task's worktree created during PHASE 0.**
* The branch created during PHASE 0 setup is the only branch you should create for this task.
* Use Git to commit your changes; leave the worktree in a clean state (`git status` shows nothing to commit).
* If pre-commit hooks fail, fix the issues and retry the commit.
* Do **not** modify or amend existing commits.
* Only committed code will be evaluated.
* Push your branch when finished and open a PR targeting the **branch you originally branched from** (automatically determined by the PR tool).

**Worktree Summary**

* The PR tool automatically targets the correct base branch based on your worktree setup.

---

## `AGENTS.md` Specification

* `AGENTS.md` files can appear anywhere (e.g., `/`, `~`, inside Git repos).
* They provide instructions or tips—coding conventions, repository structure, test commands, etc.
* They may include guidance for PR messages; **follow those instructions**.

**Scope Rules**

1. The scope of an `AGENTS.md` file is the entire directory tree rooted at its folder.
2. For every file you touch, obey instructions in any `AGENTS.md` whose scope includes that file.
3. Deeper `AGENTS.md` files override higher-level ones on conflict.
4. Direct system/developer/user instructions override `AGENTS.md`.

**Programmatic Checks**

If an `AGENTS.md` defines scripts or checks, **run them** and ensure they pass **after** all changes—even documentation-only edits.

---

## Citations Instructions

When you browse files or run terminal commands, cite them in your **final response**:

* **File citation** — `F:<file_path>†L<start>(-L<end>)?`

  * `file_path`: path relative to repo root
  * `start`/`end`: 1-indexed line numbers containing the relevant content

* **Terminal citation** — `<chunk_id>†L<start>(-L<end>)?`

  * `chunk_id`: identifier of the terminal output block

Additional rules:

* Do **not** cite empty lines.
* Do **not** cite previous PR diffs/comments, nor use Git hashes as chunk IDs.
* For PR creation tasks, use **file citations** in the summary and **terminal citations** in the testing section.
* For Q&A tasks, use terminal citations only if programmatic verification is required; otherwise use file citations.

---

You are now ready to assist.

"""


def _get_otto_metadata(key: str) -> Optional[str]:
    """Get Otto metadata from worktree-specific storage.
    
    This ensures complete isolation between concurrent worktrees.
    """
    # Only use worktree-specific storage, no fallback to git config
    return _get_worktree_metadata(key)


def _git_changed_diffs() -> list[tuple[str, str]]:
    """Return list of (filename, markdown_diff) for files changed in the worktree."""
    # Use the new helper function to get metadata
    base_commit = _get_otto_metadata('base-commit')
    if not base_commit:
        # Not in an Otto worktree, return empty list
        return []

    # Get the initial state (which captures any uncommitted changes at worktree creation)
    initial_state = _get_otto_metadata('initial-state')
    if not initial_state:
        # Fall back to base commit if initial state not found (for backwards compatibility)
        initial_state = base_commit

    try:
        # Get files changed in both committed and uncommitted states
        # First, get files with uncommitted changes (staged and unstaged)
        uncommitted_res = subprocess.run(
            ["git", "diff", "--name-only", initial_state], 
            capture_output=True, text=True
        )
        uncommitted_files = set(f.strip() for f in uncommitted_res.stdout.splitlines() if f.strip())
        
        # Also get files in staging area
        staged_res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", initial_state],
            capture_output=True, text=True  
        )
        staged_files = set(f.strip() for f in staged_res.stdout.splitlines() if f.strip())
        
        # Get untracked files
        untracked_res = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True
        )
        untracked_files = set(f.strip() for f in untracked_res.stdout.splitlines() if f.strip())
        
        # Get files changed in commits since initial state
        committed_res = subprocess.run(
            ["git", "diff", "--name-only", f"{initial_state}..HEAD"],
            capture_output=True, text=True
        )
        committed_files = set(f.strip() for f in committed_res.stdout.splitlines() if f.strip())
        
        # Combine all changed files
        all_changed_files = uncommitted_files | staged_files | committed_files | untracked_files
        
    except Exception:
        return []

    diffs: list[tuple[str, str]] = []
    for f in sorted(all_changed_files):
        try:
            # Check if file exists (it might have been deleted)
            file_exists = subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:{f}"],
                capture_output=True
            ).returncode == 0 or os.path.exists(f)
            
            if not file_exists and f not in untracked_files:
                # File was deleted
                diff = subprocess.run(
                    ["git", "--no-pager", "diff", initial_state, "--", f],
                    capture_output=True,
                    text=True,
                ).stdout
            elif f in untracked_files:
                # New untracked file - create a diff showing it as added
                try:
                    with open(f, 'r', encoding='utf-8', errors='replace') as file:
                        content = file.read()
                    lines = content.splitlines(keepends=True)
                    diff = f"diff --git a/{f} b/{f}\n"
                    diff += "new file mode 100644\n"
                    diff += "index 0000000..1111111\n"
                    diff += f"--- /dev/null\n"
                    diff += f"+++ b/{f}\n"
                    diff += "@@ -0,0 +1," + str(len(lines)) + " @@\n"
                    for line in lines:
                        diff += "+" + line
                        if not line.endswith('\n'):
                            diff += "\n"
                except Exception:
                    # If we can't read the file, skip it
                    continue
            else:
                # Get the complete diff for this file from initial state to current working directory
                # This shows all changes made by the agent, whether committed or not
                diff = subprocess.run(
                    ["git", "--no-pager", "diff", initial_state, "--", f],
                    capture_output=True,
                    text=True,
                ).stdout
                
                # If no diff in working directory, check if there are committed changes
                if not diff.strip():
                    diff = subprocess.run(
                        ["git", "--no-pager", "diff", f"{initial_state}..HEAD", "--", f],
                        capture_output=True,
                        text=True,
                    ).stdout
                    
            if diff.strip():
                # Filter out binary file diffs
                if "Binary files" not in diff:
                    diff_block = f"```diff\n{diff}\n```"
                    diffs.append((f, diff_block))
                else:
                    # For binary files, just note the change
                    diffs.append((f, "```\n[Binary file changed]\n```"))
        except Exception:
            pass
    return diffs


class CodingAgentApp(AgentApp):
    async def _process_message(self, user_message: str):
        await super()._process_message(user_message)
        
        # Only try to generate diffs if we're in an Otto worktree
        if _get_otto_metadata('base-commit'):
            # We're in a worktree, so generate diffs
            try:
                for fname, diff_md in _git_changed_diffs():
                    self.add_artifact(fname, diff_md, mode="markdown", artifact_id=fname)
            except Exception:
                # Any error during diff generation, skip it
                pass

# Build a direct Agent instance and run via AgentApp.from_agent
if __name__ == "__main__":
    import argparse

    # ----------------------
    # Parse CLI arguments
    # ----------------------
    parser = argparse.ArgumentParser(description="Multi-Tool Assistant Runner")
    parser.add_argument(
        "--mode", "-m",
        choices=["gui", "cli", "server"],
        default="cli",
        help="Run mode: 'gui' for graphical UI, 'cli' for command-line interface, 'server' to expose JSON-RPC/WebSocket endpoint"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind in server mode (default 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port for server mode (default 8000)")
    parser.add_argument("--auth-token", dest="auth_token", default=None, help="Bearer token to require from remote clients (server mode)")
    # NEW: workspace directory argument
    parser.add_argument(
        "--workspace", "-w",
        default=None,
        help="Path to the working directory to operate in. If provided, the program will 'cd' into this directory before launching the agent."
    )
    args = parser.parse_args()

    # ----------------------
    # Change working directory if requested
    # ----------------------
    if args.workspace:
        workspace_path = os.path.abspath(os.path.expanduser(args.workspace))
        if not os.path.isdir(workspace_path):
            raise ValueError(f"Provided workspace directory does not exist: {workspace_path}")
        os.chdir(workspace_path)

    # ----------------------
    # Create the single Agent (using Agents SDK directly)
    # ----------------------
    agent = Agent(
        name="Otto Code",
        model="o3",
        tools=[
            list_directory,
            glob_files,
            grep_files,
            read_file,
            edit_file,
            write_file,
            execute_bash,
            create_git_worktree,
            create_pull_request,
        ],
        instructions=get_instructions(),
        model_settings = ModelSettings(
            tool_choice="auto",
            reasoning=Reasoning(summary="concise")   # or "detailed" / "auto"
        )
    )

    # Wrap in an OmniAgents App
    app = CodingAgentApp.from_agent(
        agent,
        welcome_text="Welcome to Otto Code! I'm here to help you with your coding needs.",
        safe=True,
        safe_tool_names=[
            "list_directory",
            "glob_files",
            "grep_files",
            "read_file",
            "create_git_worktree"
        ],
        max_turns=100,
        layout="tabs"
    )

        # Launch (pass extra server args if needed)
    if args.mode == "server":
        app.run(mode="server", host=args.host, port=args.port, auth_token=args.auth_token)
    else:
        app.run(mode=args.mode)
