"""Reusable measures for terminal-task evaluation.

The primary success signal in this template is the in-container **verifier**
(exit code 0 = solved), which is used directly as the GRPO reward and reported
each step as ``rewards/sandbox_verifier/mean`` — that's the pass-rate plotted in
experiments/analyze.ipynb.

`terminal_task_solved` exposes the same verifier idea as an omniagents
``@measure`` for held-out, agent-level evaluation: given an EvalContext whose
environment ran the task in a sandbox, it passes when the verifier command
succeeded. Wire it from an EvalEnvironment that records the verifier result into
``ctx`` (see evaluations/README.md). It is intentionally environment-driven so
the *same* verifier defines both the training reward and the eval metric.
"""

from __future__ import annotations

from omniagents.notebook import EvalContext, measure, pass_reason, fail_reason


@measure
def terminal_task_solved(ctx: EvalContext):
    """Pass iff the task's in-container verifier succeeded for this rollout.

    Expects the evaluation environment to have stored a boolean
    ``verifier_passed`` in the context's expect/metadata (the EvalEnvironment
    runs the verifier in teardown). Falls back to False if absent.
    """
    passed = bool(ctx.expect.get("verifier_passed", False))
    if passed:
        return pass_reason("Verifier passed")
    return fail_reason("Verifier failed or not run")
