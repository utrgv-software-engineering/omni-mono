"""One-off specificity check: run every workspace-state measure against a
hand-completed solution workspace and confirm each one passes.

This is the second leg of EDD validation — sensitivity (catch-bad-runs) was
proved by the agent eval that put 5/5 runs in the failure column. This
script proves specificity (pass-good-runs) by running the same measures
against a workspace where the work was actually done correctly.

Run:
    python scripts/check_measures_specificity.py

Expected: every measure prints PASS.
"""

from __future__ import annotations

import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omniagents.core.evaluation import EvalContext

from evaluations import measures as M


REPO = Path(__file__).resolve().parent.parent
WORKSPACE = Path("/home/emm/scratch/fitness_solution")
FIXTURE_SRC = REPO / "evaluations" / "fixtures" / "workspaces" / "fitness_tracker"
SCENARIO_FILE = REPO / "evaluations" / "scenarios" / "autopilot_fitness_bug.yml"

# Subset of measures that depend only on workspace state — the ones we want
# to validate for specificity. The tool-call-based measures (skill_activated,
# mcp_used_to_discover_ticket, etc.) don't apply here because we have no
# agent transcript; those are validated by sensitivity already.
WORKSPACE_MEASURES = [
    "scope_respected",
    "no_test_disabling",
    "bug_caught_by_tests",
    "command_in_workspace_succeeds",
    "acceptance_artifact_exists",
    "acceptance_run_manifest_well_formed",
    "acceptance_run_completed",
    "acceptance_run_has_passing_story",
    "acceptance_per_story_manifests_consistent",
    "acceptance_run_has_evidence",
    "acceptance_run_report_rendered",
]


def _load_expect() -> dict:
    cfg = yaml.safe_load(SCENARIO_FILE.read_text())
    return cfg["scenarios"][0]["expect"]


def _make_ctx(expect: dict) -> EvalContext:
    return EvalContext(
        metadata={
            "scenario": {"expect": expect},
            "environment_context": {
                "workspace_root": str(WORKSPACE),
                "fixture_src_dir": str(FIXTURE_SRC),
            },
        },
    )


def main() -> int:
    if not WORKSPACE.is_dir():
        print(f"workspace does not exist: {WORKSPACE}")
        return 2

    expect = _load_expect()
    ctx = _make_ctx(expect)

    failures = 0
    for name in WORKSPACE_MEASURES:
        fn = getattr(M, name, None)
        if fn is None:
            print(f"SKIP {name}: not found in measures module")
            continue
        # @evaluation_measure-decorated callables expose their underlying fn.
        impl = getattr(fn, "_measure_fn", None) or fn
        try:
            result = impl(ctx)
        except Exception as exc:
            print(f"FAIL {name}: raised {type(exc).__name__}: {exc}")
            failures += 1
            continue
        passed = bool(result.get("passed"))
        reason = result.get("reason", "")
        tag = "PASS" if passed else "FAIL"
        print(f"{tag} {name}: {reason}")
        if not passed:
            failures += 1

    print()
    print(f"{'-' * 50}")
    print(f"{len(WORKSPACE_MEASURES) - failures}/{len(WORKSPACE_MEASURES)} measures passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
