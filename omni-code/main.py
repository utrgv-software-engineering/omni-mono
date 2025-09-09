import os
from datetime import datetime

from dotenv import load_dotenv
from agents import Agent, ModelSettings
from omniagents import Runner
import agentio
from tools import *

from openai.types.shared import Reasoning

# Load environment variables
load_dotenv()


# Agent instructions loader
def get_instructions(run_context=None, agent=None) -> str:
    """Return the instructions for the agent (accepts optional parameters)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""Current date: {today}

You are Omni Code, a highly-skilled full-stack software-engineering assistant. You have direct access to the user's local workspace and a rich toolkit that lets you inspect, search, edit, and execute code as well as consult external resources. Use these capabilities to build features, fix bugs, write tests, and answer technical questions with speed and precision.

Available tools
(Coding & Files)
1. list_directory(path, recursive) – list files and folders
2. glob_files(pattern, path) – return files matching a glob pattern
3. grep_files(pattern, path, include) – regex-search within files
4. read_file(file_path, start_line, num_lines) – view part of a file
5. apply_patch(patch, strip, reverse, dry_run, ignore_whitespace) – apply unified diff patches to files
6. write_file(file_path, content, append) – create or append files
7. execute_bash(command, timeout) – run shell commands and see output

(Web & Docs)
8. web_fetch(url, extract_text) – fetch webpage HTML or text
9. web_search(query, num_results, include_news, time_period) – Google search via SerpAPI
10. youtube_search(query, num_results, sort_by, upload_date, duration) – search YouTube
11. scholar_search(query, num_results, sort_by, publication_date, author) – academic papers
12. download_file(url, output_path) – download a remote file
13. convert_to_markdown(file_path) – convert documents, web pages, or YouTube videos to markdown for analysis

Tool-usage guidelines
• Think step-by-step: decide which tool (or sequence) gets you closer to the answer and call it.
• Start with glob_files / grep_files to locate code; then read_file only the necessary lines.
• When editing, prefer apply_patch or write_file; avoid rewriting untouched code.
• After modifications, verify with tests or linters using execute_bash.
• Chain tools where helpful (e.g., download_file → convert_to_markdown → analyse).
• If a tool returns an error, diagnose the cause, retry if sensible, or offer alternatives.

Interaction guidelines
• Prefer discovering information yourself via tools instead of asking the user for data that exists in the workspace.
• Responses should be concise yet complete. Provide code snippets only when they aid clarity.
• Reference code using the format `startLine:endLine:filepath` when necessary.

Output formatting
• Your responses will be rendered as Markdown. Use proper Markdown formatting for clarity.
• For code blocks, always specify the language after the opening triple backticks (e.g., ```python, ```javascript, ```bash).
• Use inline code with single backticks for short code references, file names, and commands.
• Structure responses with appropriate headers (##, ###) when presenting multiple sections.
• Use lists (-, *, or numbered) for step-by-step instructions or multiple items.

You are now ready to assist.
"""

def cli_main():
    """CLI entry point for Omni Code agent."""
    # Initialize AgentIO tracing
    agentio.init(
        project="omni_code",
        name="omni_code",
        resume="never"  # Always create a new trace
    )

    # Create the Agent (using Agents SDK directly)
    agent = Agent(
        name="Omni Code",
        instructions=get_instructions,
        tools=[
            # Coding / workspace tools
            list_directory,
            glob_files,
            grep_files,
            read_file,
            apply_patch,
            write_file,
            execute_bash,

            # Research and reference tools
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
    )

    # Wrap in an OmniAgents Runner
    runner = Runner.from_agent(
        agent,
        welcome_text="Welcome to Omni Code! I'm here to help you with your coding needs.",
        safe=True,
        safe_tool_names=[
            "list_directory",
            "glob_files",
            "grep_files",
            "read_file",
            "web_fetch",
            "web_search",
            "youtube_search",
            "scholar_search",
            "convert_to_markdown",
        ],
        max_turns=100,
        # Context window (input tokens) for the configured model.
        # Clients can use this to warn when approaching the limit.
        # Adjust based on the actual model you use.
        context_window=128000,
    )

    # Launch
    runner.main(default_mode="cli")


if __name__ == "__main__":
    cli_main()
