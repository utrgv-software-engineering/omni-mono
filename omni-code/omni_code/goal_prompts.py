"""Goal-loop prompt builders for the tick-driven autopilot.

The loop has two prompts:

- **Initial** (``build_goal_initial_prompt``): user-role message that
  kicks off the first run. Frames the agent as working toward a goal
  with the ``goal_complete`` tool contract spelled out and the tick
  model explained.
- **Tick** (``build_goal_tick_prompt``): short assistant-role
  notification delivered by the periodic tick scheduler. The agent
  reads it as its own infrastructure poking to take the next step.

The goal text itself is free-form prose — the user packs the
verification surface, constraints, boundaries, iteration policy, and
blocked-stop condition into the prompt and the agent reads it from
history each turn. We do not parse or structure the text.
"""

from __future__ import annotations


def build_goal_initial_prompt(goal: str, max_turns: int) -> str:
    """First user message of an autopilot loop. Frames the goal, the
    tool contract for termination (including the optional evidence
    field), and the tick model so the agent knows it's expected to
    keep working between wake-ups."""
    return f"""You are working toward a specific goal across multiple turns. Use your judgment, the tools, and the workspace to make steady progress until the goal is achieved or the turn budget is exhausted.

## Goal
{goal}

## Reading the goal

Treat the goal text above as your operating contract. A well-written goal names what should be true when done, how to verify it, what must not regress, what's in scope, how to choose the next step between iterations, and what to report if blocked. Re-read it each turn — your history is your durable memory of the contract.

## Approach

1. **Explore** — Read the codebase to understand structure and conventions.
2. **Plan** — Break the goal into concrete, testable steps.
3. **Execute** — Use `spawn_worker` for independent subtasks, `execute_bash` for commands, `apply_patch` / `write_file` for edits.
4. **Verify** — Run tests, lint, and type-check the results against whatever verification surface the goal names.
5. **Iterate** — Each turn picks up from session history. Front-load exploration and batch related changes.

## Operating Context

- You are running in an isolated sandbox with full read/write access to the workspace.
- The user has delegated implementation decisions to you. When ambiguity is material, document your interpretation in your response and proceed with the most reasonable approach.
- Prefer making progress over waiting for input. Use `notify` for heads-up messages and `escalate` only when truly blocked on human input.

## How the loop wakes you

A periodic tick wakes you between turns. You'll receive a short tick notification when the loop wants you to take the next step. You'll also wake automatically when background bash jobs finish or workers complete. If you have nothing left to do, call `goal_complete`.

## Termination

Call the `goal_complete` tool once the goal is achieved and verified:

    goal_complete(reason="achieved", evidence="<what you checked and what it showed>")

Call it also if you reach a true blocker (missing credentials, external system unavailable, contradictory requirements):

    goal_complete(reason="blocked", evidence="<what's missing and what would unblock progress>")

The ``evidence`` field is auditable — cite the test name + result, benchmark number, generated artifact path, or specific blocker. It surfaces in the goal panel and /goal.status output. The tool call stops the loop — no further tick notifications will arrive. It renders nothing in the transcript; the goal panel reflects the terminal state on its own.

## Budget

You have up to {max_turns} turns before the loop self-terminates. Reaching the budget is not the same as completing the goal — if it expires, summarize progress and remaining work in your final turn.
""".strip()


def build_goal_tick_prompt(*, goal: str, max_turns: int) -> str:
    """Periodic-tick notification body. Short by design — the agent
    already has the goal + context in history; this is just a poke to
    take the next step."""
    return (
        f"[goal] Tick. Goal: {goal} (budget {max_turns} turns total). "
        "Take the next concrete step. Reuse context — don't re-read files you have. "
        "If the goal is achieved or verified, call `goal_complete(reason=\"achieved\", evidence=\"...\")`. "
        "If you're truly blocked on something requiring human input, use `escalate` "
        "or call `goal_complete(reason=\"blocked\", evidence=\"...\")`. "
        "For long-running work, kick it off with `execute_bash(background=True)` or "
        "`spawn_worker(...)` and end your turn — the completion notification will wake you."
    )
