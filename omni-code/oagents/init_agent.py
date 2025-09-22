from __future__ import annotations

from typing import Optional

from omniagents import Runner
from omniagents.core.agents.safe import SafeAgent

from tools import (
    list_directory,
    glob_files,
    grep_files,
    read_file,
    apply_patch,
    write_file,
)
from importlib import resources


def load_instructions() -> str:
    try:
        with resources.files("oagents").joinpath("instructions/init_agent.md").open(
            "r", encoding="utf-8"
        ) as f:
            return f.read().strip()
    except Exception:
        pass

    with open("instructions/init_agent.md", "r", encoding="utf-8") as f:
        return f.read().strip()


def get_prompt() -> str:
    return (
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


def create_init_agent() -> SafeAgent:
    return SafeAgent(
        name="AGENTS.md Init Agent",
        instructions=load_instructions(),
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


async def run_init_agent(max_turns: int = 50) -> str:
    agent = create_init_agent()
    prompt = get_prompt()
    result = await Runner.run(agent, prompt, max_turns=max_turns)
    try:
        return result.final_output_as(str)
    except Exception:
        return "Failed to update AGENTS.md"
