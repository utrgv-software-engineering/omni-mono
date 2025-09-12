from __future__ import annotations

from typing import Optional

from omniagents.core.tools import function_tool


@function_tool(client_status="Running command...")
async def invoke_slash_command(command: str, arg: Optional[str] = None) -> str:
    """Invoke a slash command with optional args.

    Called when the user's input starts with "/". Runs inside the agent's
    current run so the TUI can stream progress and avoid separate RPC timeouts.
    """
    norm = (command or "").strip().lower()

    if norm == "init":
        # Build and run the subagent that generates or improves AGENTS.md
        from agents import Runner
        from omniagents.core.agents.safe import SafeAgent
        # Import workspace tools for the subagent
        from tools import (
            list_directory,
            glob_files,
            grep_files,
            read_file,
            apply_patch,
            write_file,
        )

        init_instructions = (
            """
You are an AGENTS.md author. Generate or improve AGENTS.md for this repository.
- If any AGENTS.md exists, read and improve it: clarify scope, precedence, and concrete repo conventions.
- If none exists, create one at the project root covering: Title, Purpose, Scope, Precedence, Code Style, Tools/Libs, Testing/Running, and repo-specific conventions you infer.
- Discover repo structure with list_directory, glob_files, grep_files, read_file.
- Apply changes using apply_patch or write_file. Deliver a final patch that writes/updates AGENTS.md.

Final response policy:
- Your final message must be EXACTLY one of the following single-line strings (no quotes, no code blocks, no extra text):
  - Created AGENTS.md
  - Update AGENTS.md
  - Failed to create AGENTS.md
  - Failed to update AGENTS.md
- Choose based on what actually happened:
  - Use "Created AGENTS.md" only if you created a new AGENTS.md.
  - Use "Update AGENTS.md" only if you modified an existing AGENTS.md.
  - If a create attempt did not apply or failed, use "Failed to create AGENTS.md".
  - If an update attempt did not apply or failed, use "Failed to update AGENTS.md".
"""
        ).strip()

        init_agent = SafeAgent(
            name="AGENTS.md Init Agent",
            instructions=init_instructions,
            tools=[
                list_directory,
                glob_files,
                grep_files,
                read_file,
                apply_patch,
                write_file,
            ],
            model="gpt-5",
            approval_callback=SafeAgent.get_global_approval_callback(),
            safe_tool_names=[
                "list_directory",
                "glob_files",
                "grep_files",
                "read_file",
                "apply_patch",
            ],
            halt_on_rejection=True,
            skip_approvals=False,
        )

        prompt = (
            "Generate or improve AGENTS.md for this repository.\n"
            "- If AGENTS.md exists anywhere, read and improve it.\n"
            "- If not, create one at the project root with Purpose, Scope, Precedence, Code Style, Tools, Testing/Running, and repo conventions.\n"
            "- Use list_directory, glob_files, grep_files, read_file.\n"
            "- Apply changes using apply_patch or write_file.\n"
            "Deliver a final patch writing/updating AGENTS.md.\n\n"
            "Final response: Output EXACTLY ONE of these single lines, and nothing else:\n"
            "- Created AGENTS.md\n"
            "- Update AGENTS.md\n"
            "- Failed to create AGENTS.md\n"
            "- Failed to update AGENTS.md\n"
            "Select the line that reflects the actual outcome."
        )

        result = await Runner.run(init_agent, prompt, max_turns=50)
        try:
            return result.final_output_as(str)  # Prefer textual final output
        except Exception:
            return "Failed to update AGENTS.md"

    return f"Unknown command: {command}"
