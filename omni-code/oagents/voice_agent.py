import os
from datetime import datetime
from importlib import resources

from openai.types.shared import Reasoning
from omniagents.core.agents.safe import SafeAgent
from omni_code.config import get_model_name
from agents import ModelSettings
from omni_code.tools_bootstrap import ensure_tools_importable

ensure_tools_importable()

from tools import (
    dispatch,
    list_directory,
    glob_files,
    grep_files,
    read_file,
    apply_patch,
    write_file,
    execute_bash,
    web_fetch,
    web_search,
    youtube_search,
    scholar_search,
    download_file,
    convert_to_markdown,
)
from oagents.instruction_utils import format_instructions


def _load_instructions() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    text: str | None = None
    try:
        with (
            resources.files("oagents")
            .joinpath("instructions/voice_agent.md")
            .open("r", encoding="utf-8") as handle
        ):
            text = handle.read()
    except Exception:
        pass
    if text is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base_dir, "instructions", "voice_agent.md")
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    return text.replace("{{today}}", today)


def get_instructions(run_context=None, agent=None) -> str:
    base = _load_instructions()
    return format_instructions(base)


def create_voice_agent(
    *, skip_approvals: bool = False, halt_on_rejection: bool = True
) -> SafeAgent:
    return SafeAgent(
        name="Omni Code Voice",
        instructions=get_instructions,
        tools=[
            dispatch,
            list_directory,
            glob_files,
            grep_files,
            read_file,
            apply_patch,
            write_file,
            execute_bash,
            web_fetch,
            web_search,
            youtube_search,
            scholar_search,
            download_file,
            convert_to_markdown,
        ],
        model=get_model_name(),
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="high"),
        ),
        safe_tool_names=[
            "list_directory",
            "glob_files",
            "grep_files",
            "read_file",
            "dispatch",
            "web_fetch",
            "web_search",
            "youtube_search",
            "scholar_search",
            "convert_to_markdown",
        ],
        halt_on_rejection=halt_on_rejection,
        skip_approvals=skip_approvals,
    )
