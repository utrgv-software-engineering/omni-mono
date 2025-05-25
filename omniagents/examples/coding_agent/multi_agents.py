"""Defines a manager/worker style multi-agent setup for Otto Code.

Each phase of the autonomous workflow is delegated to a specialised sub-agent
with a restricted tool-set.  The *manager* agent orchestrates those workers
via tool-functions that call them, making the overall system appear as a
single agent to the outside world while still benefiting from specialisation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from agents import Agent, Runner
from omniagents.core import function_tool

# Workspace / shell tools -----------------------------------------------------
from tools.file_tools import (
    list_directory,
    glob_files,
    grep_files,
    read_file,
    edit_file,
    write_file,
)
from tools.bash_tool import execute_bash

# ---------------------------------------------------------------------------
# Helper to pick a default model – fall back gracefully if enterprise names
# are not available.
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "o3"  # Use the same default as existing single-agent setup


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# Sub-agent definitions
# ---------------------------------------------------------------------------

# Phase 1-4 : repository reconnaissance -------------------------------------------------
scanner_agent = Agent(
    name="Project Scanner",
    model=DEFAULT_MODEL,
    tools=[list_directory, glob_files, grep_files, read_file],
    instructions=f"""Current date: {_today()}
You are the *Project Scanner* – a specialised agent responsible for phases 1-4
of the workflow:
 1. Look for `AGENTS.md` / `agents.md` files that may contain special
    instructions.
 2. Detect languages, frameworks, runtime managers (e.g. pyenv, nvm) and major
    dependencies used in the project.
 3. Identify test / linter setups that act as safety-nets.
 4. Produce a clear summary of the overall codebase structure (folders,
    modules, entry-points, etc.)

You have **read-only** access to the workspace through the provided tools.  Do
NOT attempt to modify any files or execute shell commands – your only goal is
information gathering and returning a comprehensive, structured report.""",
)

# Phase 5 : deep exploration ---------------------------------------------------
explorer_agent = Agent(
    name="Deep Explorer",
    model=DEFAULT_MODEL,
    tools=[list_directory, glob_files, grep_files, read_file],
    instructions=f"""Current date: {_today()}
You are the *Deep Explorer* – a specialised agent responsible for phase 5.
Take the high-level structure and requirements discovered by the Scanner and
recursively inspect implementation files, auto-generated artefacts (e.g.
`site-packages`, `node_modules`, `.venv`), and anything else that may be
relevant.  Surface any helper utilities, framework glue-code, or patterns that
will be important when implementing changes later.  Output an *annotated file
list* with short notes describing what each discovered element does, together
with links (path + line numbers) for quick reference. Do **not** change the
workspace.""",
)

# Phase 6 : implementation -----------------------------------------------------
coder_agent = Agent(
    name="Coder",
    model=DEFAULT_MODEL,
    tools=[read_file, edit_file, write_file, list_directory, glob_files, grep_files, execute_bash],
    instructions=f"""Current date: {_today()}
You are the *Coder* – a specialised agent that receives a concrete task plus
all the contextual information from previous phases.  Implement the required
changes in the most idiomatic way for the detected stack.  Make focussed,
minimal edits; run linters / formatters when available; and prepare tests so
that the verification phase succeeds.

When you invoke shell commands use `execute_bash` and capture the output so
that later phases can inspect it.  Commit messages are *not* your concern –
just change the files in-place.  After finishing, return a short "changeset
summary" describing every file that was touched and why.""",
)

# Phase 7 : verification / clean-up -------------------------------------------

tester_agent = Agent(
    name="Verifier",
    model=DEFAULT_MODEL,
    tools=[execute_bash, list_directory, grep_files, read_file],
    instructions=f"""Current date: {_today()}
You are the *Verifier* – a specialised agent responsible for phase 7.
Run the projectʼs test-suite(s) and linters, interpret the results, and decide
whether the Coderʼs changes are correct.  If problems are found, provide a
clear report so that the Manager can send the task back to the Coder with
specific guidance.

If everything is green, clean up temporary artefacts (log files, debugging
printouts, stray files) **without** touching legitimate project files.
Finish with a short success/failure verdict.""",
)

# ---------------------------------------------------------------------------
# Delegation tools – exposed to the manager agent
# ---------------------------------------------------------------------------

@function_tool
async def phase1_4_scan(task_overview: str) -> str:
    """Delegate phases 1-4 to *scanner_agent* and return its report."""
    result = await Runner.run(scanner_agent, task_overview, max_turns=100)
    return result.final_output  # type: ignore[attr-defined]


@function_tool
async def phase5_explore(scanner_report: str) -> str:
    """Delegate phase 5 to *explorer_agent* supplying the scannerʼs output."""
    prompt = (
        "Using the following high-level project information, perform a deep "
        "exploration of the code-base to create an annotated file map.\n\n" + scanner_report
    )
    result = await Runner.run(explorer_agent, prompt, max_turns=100)
    return result.final_output  # type: ignore[attr-defined]


@function_tool
async def phase6_code(task_description: str, context: str) -> str:
    """Delegate phase 6 (implementation) to *coder_agent*."""
    prompt = (
        f"Task to implement:\n{task_description}\n\n"
        f"Context collected from earlier phases:\n{context}\n"
    )
    result = await Runner.run(coder_agent, prompt, max_turns=100)
    return result.final_output  # type: ignore[attr-defined]


@function_tool
async def phase7_verify(context: str) -> str:
    """Delegate verification / clean-up to *tester_agent*."""
    result = await Runner.run(tester_agent, context, max_turns=100)
    return result.final_output  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Manager agent – orchestrates the full autonomous workflow
# ---------------------------------------------------------------------------

def create_manager_agent() -> Agent:
    """Return an *Otto Code Manager* agent that orchestrates the pipeline."""

    return Agent(
        name="Otto Code (Manager)",
        model=DEFAULT_MODEL,
        tools=[
            phase1_4_scan,
            phase5_explore,
            phase6_code,
            phase7_verify,
        ],
        instructions=f"""Current date: {_today()}
You are *Otto Code – Manager*, the overseer in a multi-agent autonomous coding
system.  When a user provides a task you must:

PHASE 1-4  → call `phase1_4_scan` with the original user request and record
             the returned **Scanner Report**.
PHASE 5    → call `phase5_explore` using the *Scanner Report* and record the
             **Explorer Map**.
PHASE 6    → call `phase6_code` supplying the original request + both previous
             reports. The result is the **Coder Changeset**.
PHASE 7    → call `phase7_verify` with everything collected so far.  The result
             is the **Verifier Verdict**.

After all phases conclude, create a *comprehensive summary* for the user that
states whether the task was completed successfully, what was changed, and how
it was validated.  If the Verifier reported problems, inform the user and
suggest next steps – **do not** attempt to fix issues yourself in the Manager.

You are strictly a coordinator: never invoke workspace tools directly, always
use the delegation functions in your tool-box.""",
    )
