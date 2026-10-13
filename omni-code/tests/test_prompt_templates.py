"""Byte-parity gates for the prompts lifted from Python into
``omni_code/prompts/*.md``.

The legacy builders (``omni_code/goal_prompts.py`` /
``omni_code/loop_prompts.py``) are deleted; their exact output is
reproduced here verbatim as the fixture. The framework must render the
packaged template files to byte-identical strings — whitespace-exact —
through its conventional resolution (no builders installed, the
discovered omni-code product manifest's ``paths.prompts``).
"""

from omniagents.core.autonomy import goal as goal_mod
from omniagents.core.autonomy import loop as loop_mod

# ---------------------------------------------------------------------------
# Legacy builders, copied verbatim from the deleted omni_code/goal_prompts.py
# and omni_code/loop_prompts.py. Do not edit except in lockstep with the
# template files — this file IS the parity fixture.
# ---------------------------------------------------------------------------


def _legacy_goal_initial(goal: str, max_turns: int) -> str:
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

The ``evidence`` field is auditable — cite the test name + result, benchmark number, generated artifact path, or specific blocker. It surfaces in the goal panel and `/goal` output. The tool call stops the loop — no further tick notifications will arrive. It renders nothing in the transcript; the goal panel reflects the terminal state on its own.

## Budget

You have up to {max_turns} turns before the loop self-terminates. Reaching the budget is not the same as completing the goal — if it expires, summarize progress and remaining work in your final turn.
""".strip()


def _legacy_goal_tick(*, goal: str, max_turns: int) -> str:
    return (
        f"[goal] Tick. Goal: {goal} (budget {max_turns} turns total). "
        "Take the next concrete step. Reuse context — don't re-read files you have. "
        'If the goal is achieved or verified, call `goal_complete(reason="achieved", evidence="...")`. '
        "If you're truly blocked on something requiring human input, use `escalate` "
        'or call `goal_complete(reason="blocked", evidence="...")`. '
        "For long-running work, kick it off with `execute_bash(background=True)` or "
        "`spawn_worker(...)` and end your turn — the completion notification will wake you."
    )


_LEGACY_MAINTENANCE_PROMPT = """Continue useful maintenance for this session.

Work in this order:
1. Continue any unfinished work already authorized in the conversation.
2. Tend to the current branch or pull request when discoverable: failed checks, review comments, merge conflicts, or obvious regressions.
3. Check background jobs, workers, and queued follow-up work that the transcript already started.
4. If nothing needs action, say so briefly and stop.

Do not start unrelated new initiatives. Do not push, delete, merge, or perform irreversible actions unless the transcript already authorized that exact class of action.""".strip()


# A goal text with multiple lines and Markdown punctuation, to make the
# parity check meaningful beyond trivial substitution.
_FIXTURE_GOAL = (
    "Ship the `/init` lift & verify:\n"
    "- all 193 tests green\n"
    "- no *regressions* in wheel packaging"
)
_FIXTURE_MAX_TURNS = 37


def test_goal_initial_template_byte_parity():
    got = goal_mod.build_initial_prompt(_FIXTURE_GOAL, _FIXTURE_MAX_TURNS)
    assert got == _legacy_goal_initial(_FIXTURE_GOAL, _FIXTURE_MAX_TURNS)


def test_goal_tick_template_byte_parity():
    got = goal_mod.build_tick_prompt(goal=_FIXTURE_GOAL, max_turns=_FIXTURE_MAX_TURNS)
    assert got == _legacy_goal_tick(goal=_FIXTURE_GOAL, max_turns=_FIXTURE_MAX_TURNS)


def test_loop_packaged_template_byte_parity(tmp_path, monkeypatch):
    # Neutralize the operator override layers so the packaged
    # omni_code/prompts/loop.md is what resolves: an empty XDG config
    # home and a workspace without .omni/loop.md.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    got = loop_mod.resolve_default_loop_prompt(str(ws))
    assert got == _LEGACY_MAINTENANCE_PROMPT


def test_loop_workspace_override_still_wins(tmp_path, monkeypatch):
    # The historical candidate paths survive the lift: <workspace>/.omni/
    # (derived from the product prog) ahead of the packaged template.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    (ws / ".omni").mkdir(parents=True)
    (ws / ".omni" / "loop.md").write_text("operator body\n", encoding="utf-8")
    assert loop_mod.resolve_default_loop_prompt(str(ws)) == "operator body"
