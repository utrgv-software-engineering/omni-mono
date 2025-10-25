from __future__ import annotations

from typing import Optional

from agents import Runner
from omniagents.core.agents.safe import SafeAgent
from omni_code.config import get_model_name
from omni_code.tools_bootstrap import ensure_tools_importable

ensure_tools_importable()

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
        with (
            resources.files("oagents")
            .joinpath("instructions/init_agent.md")
            .open("r", encoding="utf-8") as f
        ):
            return f.read().strip()
    except Exception:
        pass

    with open("instructions/init_agent.md", "r", encoding="utf-8") as f:
        return f.read().strip()


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
        model=get_model_name(),
        approval_callback=SafeAgent.get_global_approval_callback(),
        safe_tool_names=[
            "list_directory",
            "glob_files",
            "grep_files",
            "read_file",
        ],
        halt_on_rejection=True,
        skip_approvals=False,
    )


async def run_init_agent(max_turns: int = 1000) -> str:
    agent = create_init_agent()
    result = await Runner.run(agent, "begin", max_turns=max_turns)
    try:
        return result.final_output_as(str)
    except Exception:
        return "Failed to update AGENTS.md"
