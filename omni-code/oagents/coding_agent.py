import os
from datetime import datetime
from typing import List, Tuple
from importlib import resources

from openai.types.shared import Reasoning
from omniagents.core.agents.safe import SafeAgent
from agents import ModelSettings
from tools import (
    invoke_slash_command,
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


def _load_instructions() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    text: str | None = None

    try:
        with resources.files("oagents").joinpath("instructions/coding_agent.md").open(
            "r", encoding="utf-8"
        ) as f:
            text = f.read()
    except Exception:
        pass

    if text is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base_dir, "instructions", "coding_agent.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

    return text.replace("{{today}}", today)


def _collect_agents_md_downwards(start_dir: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    for root, dirs, files in os.walk(start_dir):
        if "AGENTS.md" in files:
            path = os.path.join(root, "AGENTS.md")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    found.append((path, f.read()))
            except Exception:
                pass
    found.sort(key=lambda x: (len(os.path.relpath(os.path.dirname(x[0]), start_dir).split(os.sep)) if os.path.dirname(x[0]) != start_dir else 0, x[0]))
    return found


def get_instructions(run_context=None, agent=None) -> str:
    base = _load_instructions()
    cwd = os.getcwd()
    agents_files = _collect_agents_md_downwards(cwd)
    # Always include a Jinja placeholder for workspace; SilentUndefined => empty when missing
    if not agents_files:
        return base + "\n\n[Workspace]\n{{ workspace_root }}\n"
    parts = [base, "\n\n[Workspace]\n{{ workspace_root }}\n", "\n\n# Repository AGENTS.md (parent → child)\n"]
    for path, text in agents_files:
        parts.append(f"\n[AGENTS.md at {os.path.dirname(path)}]\n{text}")
    return "".join(parts)


def create_agent() -> SafeAgent:
    return SafeAgent(
        name="Omni Code",
        instructions=get_instructions,
        tools=[
            invoke_slash_command,
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
        model="gpt-5",
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="high"),
        ),
        safe_tool_names=[
            "list_directory",
            "glob_files",
            "grep_files",
            "read_file",
            "invoke_slash_command",
            "web_fetch",
            "web_search",
            "youtube_search",
            "scholar_search",
            "convert_to_markdown",
        ],
        halt_on_rejection=True,
        skip_approvals=False,
    )
