# Eval Design

The principles behind the omni-code evaluation harness.

This doc is the conceptual layer: **what makes an eval worth running**, **how to design one from scratch**, and **how to read the measures we already have**. The operator's guide — how to run evals, inspect transcripts, work with fixtures — lives at [`evaluations/AGENTS.md`](../evaluations/AGENTS.md).

The harness exists to be a **CI gate** for agentic coding behavior. When a change merges to omni-code, we want hard signal that the agent still gets things done, that it gets them done well, and that it doesn't do things we've decided it must not do. Anything that doesn't earn its seat in that loop should not be in the suite.

---

## 1. Quality criteria — five lenses every measure must pass

A measure earns its place by passing all five.

1. **Specific.** Asserts a concrete observable with a falsifiable verifier. ✓ "Regression test fails when source is reverted." ✗ "Agent was helpful." If you can't write the verifier, the property doesn't belong in the eval.

2. **Actionable.** A 20% move in this score must map to a concrete team decision. The acid test: *"if this halved tomorrow, what would we change?"* If the answer is "nothing," the measure is dead weight on every CI run.

3. **Relevant.** Verify outcomes; don't micromanage process. Sad paths must be ones we have **observed in the wild** OR have **legal / security / compliance** teeth. Imagined failure modes don't earn their seat.

4. **Time-boxed.** Hard wall-clock cap per scenario. Configurable per-measure when work is genuinely heavy (e.g. `command_timeout_s`, `test_timeout_s`). Evals consume tokens and engineer attention; both are budgets.

5. **Reproducible.** Same fixture + same agent → comparable score. Non-determinism is handled by `runs_per_prompt` + aggregation, not by suppressing noisy measures.

### Pruning heuristic for Guards

A **Guard** measure (anti-action — "agent did NOT do X") that has never fired across CI history AND represents no live risk is a deletion candidate. Keep only Guards backed by an observed incident, a compliance requirement, or an active probe of dangerous behavior.

### What we explicitly do not measure

Friendliness. Bias. Helpfulness. Toxicity. These are vague qualities for general-purpose chatbots. omni-code is an agentic coding system; what matters is whether the agent reaches the desired end state and whether the trace contains things it must not contain. Personality lenses fail the Specific and Actionable criteria simultaneously.

---

## 2. End-state-first design

Don't design from the prompt forward. Design from the end state backward.

```
1. Capture done.
   The end state, in any artifact: codebase snapshot, golden screenshot,
   reference document, git history, video, transcript. The richer the
   artifact, the more measures you can derive.

2. Define what "done means" via verifiers.
   For each property of the end state that matters, name a verifier:
     - Deterministic (pytest, byte compare, SQL exists)
     - Threshold (latency < N, count, presence)
     - Oracle (LLM judge, human review) — last resort
   No verifier → the property doesn't belong in the eval.

3. Derive the minimal seed.
   Work backwards from done to the SMALLEST context (prompt + files +
   skills) that should reliably elicit it under a well-functioning agent.
   Don't over-spec — generalize beyond the specific instance.

4. Build the harness.
   Fixture + measures + scenario YAML + aggregation.

5. Sensitivity test (catches bad runs).
   Run an agent (or a known-bad fixture mutation). Measures must fire.

6. Specificity test (passes good runs).
   Run a hand-completed solution. Every measure passes for the right
   reasons. See `scripts/check_measures_specificity.py` for the pattern.

7. Hill-climb on prompts/skills only after 5 + 6 are green.
```

This generalizes beyond software. Swap "codebase" for "deck," "design," or "document." Swap "pytest" for "image diff," "ETL row count," or whatever the artifact verifier is. The principle is invariant: **define done first, then ask whether you can detect it, then ask what minimum input should produce it.**

Steps 5 and 6 are non-negotiable. A measure that has never been validated for both **sensitivity** (catches genuine failures) and **specificity** (passes genuine successes) is a measure you don't trust yet.

---

## 3. Tier hierarchy — Outcome / Quality / Process / Guard

There is an eval pyramid, but it's not unit→integration→e2e. The right axes are **artifact vs trace** and **load-bearing vs diagnostic**.

| Tier        | Question                                                     | Inspects                  | CI gate?         |
| ----------- | ------------------------------------------------------------ | ------------------------- | ---------------- |
| **Outcome** | Did the desired end state get reached?                       | Final artifact            | Yes              |
| **Quality** | Is the artifact well-formed beyond bare correctness?         | Final artifact (structural) | Yes              |
| **Process** | Did the agent take legitimate paths?                         | Action trace              | **No** — diagnostic |
| **Guard**   | Did the agent NOT do a forbidden thing?                      | Action trace (negation)   | Yes (sparingly)  |

Heuristics:

- **Most measures should be Outcome or Quality.** Those are what the team actually cares about — the artifact in front of them.
- **Process measures are diagnostic, not goals.** They explain *why* an Outcome dropped; they're cross-tab variables, not success criteria. Don't reward "read the right skill" — reward "shipped a passing acceptance run." If skill-reading correlates with success, you'll see it in the cross-tab. If it doesn't, who cares.
- **Guards stay sparse and load-bearing.** Each one must clear the prune heuristic in §1.
- **Existence checks subsumed by structural checks should be dropped.** If `acceptance_run_report_rendered` (existence + non-trivial size + runId match) passes, then `acceptance_artifact_exists` (file present) is redundant — keep the stricter one.

---

## 4. Worked example — autopilot fitness bug

The scenario at `evaluations/scenarios/autopilot_fitness_bug.yml`, walked through the framework.

### End state captured

- Workspace fixture `evaluations/fixtures/workspaces/fitness_tracker/` — FastAPI + React + uv project with a planted bug in `src/fitness_tracker/routers/workouts.py::get_recent_workouts` (sorts by `created_at` instead of `workout_date`).
- DB fixture `evaluations/fixtures/dbs/fitness_bug_ticket.db` — seeded omni-projects DB with one project, one ticket whose description spells out the three-layer acceptance: source fix + API regression test + UI acceptance run.
- Pristine baseline + ticket text together encode "what done looks like."

### Verifiers (what done *means*)

| Property of done                                     | Verifier                                                                                  | Tier    |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------- |
| Source bug fixed                                     | `bug_caught_by_tests` — revert router, rerun pytest, expect non-zero                      | Outcome |
| Test suite still green                               | `command_in_workspace_succeeds` — `uv run pytest -q` exits 0                              | Outcome |
| Ticket reached terminal column                       | `db_row_exists` — SQL on the seeded DB                                                    | Outcome |
| Acceptance run completed end-to-end                  | `acceptance_run_completed` — every story has terminal status                              | Outcome |
| Acceptance run produced a passing story              | `acceptance_run_has_passing_story` — ≥1 story with `status=pass`                          | Outcome |
| No out-of-scope file changes                         | `scope_respected` — git diff against pristine fixture matches allow-lists                 | Quality |
| Acceptance manifest is well-formed                   | `acceptance_run_manifest_well_formed` — schema check on `run.manifest.json`               | Quality |
| Per-story manifests are internally consistent        | `acceptance_per_story_manifests_consistent` — top/per-story IDs match                     | Quality |
| Real screenshot evidence on disk                     | `acceptance_run_has_evidence` — checkpoint paths resolve to real PNGs                     | Quality |
| Run report HTML exists, sized right, references runId | `acceptance_run_report_rendered`                                                          | Quality |
| Agent discovered ticket via the right channel        | `mcp_used_to_discover_ticket` — at least one `list_tickets`/`get_ticket` call             | Process |
| Agent walked the pipeline (no Backlog→Done jump)     | `ticket_moved_through_pipeline` — ≥2 `move_ticket` calls                                  | Process |
| Each tracked skill was activated                     | `<skill>_skill_activated` (debug, software-planning, bugfix)                              | Process |
| No remote git / GitHub operations                    | `no_remote_operations` — bash-command pattern check                                       | Guard   |
| No tests deleted or `@skip`/`@xfail` added           | `no_test_disabling` — count diff vs pristine                                              | Guard   |

### Minimal seed

The eval scenario's `prompt` is generic supervisor framing modeled on the launcher's [`buildSupervisorPrompt`](../../launcher/src/main/supervisor-prompt.ts) — Critical Rules, How to Work, generic pipeline language, plus a Discovery step (eval-only, since the agent must MCP-find the ticket itself) and the `[DONE]` sentinel for the framework's stop condition. **All project-specific context lives in the ticket description**, so swapping the planted bug doesn't require touching the scenario YAML.

### Sensitivity validation

Run the eval against an agent. With the buggy fixture and an unmodified agent, the new acceptance measures should fire. We've seen this: across `runs_per_prompt: 5`, every run failed `bug_caught_by_tests` (agent wrote false-green pytest) and `acceptance_run_has_passing_story` (agent never produced a green acceptance run). Sensitivity ✓.

### Specificity validation

Run the workspace-state measures against a hand-completed solution and confirm every one passes for the right reason. Pattern lives at `scripts/check_measures_specificity.py`:

```python
ctx = EvalContext(metadata={
    "scenario": {"expect": expect_block_from_scenario_yaml},
    "environment_context": {
        "workspace_root": "/path/to/solution_workspace",
        "fixture_src_dir": "/path/to/pristine_fixture",
    },
})
for name in WORKSPACE_MEASURES:
    impl = getattr(M, name)._measure_fn or getattr(M, name)
    result = impl(ctx)
    assert result["passed"], result["reason"]
```

This validation pass found **two real bugs**:
- Measure bug: `acceptance_run_has_evidence` resolved checkpoint paths against the eval process's CWD, not the workspace root, so it false-failed when `--out-root` was relative. Fixed.
- Runner bug (in the `webapp-acceptance-runner` skill): `expectTextEquals` and similar emit `const __textEq` per step, so two of the same kind in one story → `SyntaxError: Identifier '__textEq' has already been declared`. Worked around in the story; runner template should be patched.

That's the value of EDD: validating measures surfaces problems in the things measures *depend on*.

### Tier mapping (current state, 18 measures)

- **Outcome (5):** `db_row_exists`, `bug_caught_by_tests`, `command_in_workspace_succeeds`, `acceptance_run_completed`, `acceptance_run_has_passing_story`
- **Quality (6):** `scope_respected`, `acceptance_run_manifest_well_formed`, `acceptance_per_story_manifests_consistent`, `acceptance_run_has_evidence`, `acceptance_run_report_rendered`, `acceptance_artifact_exists`
- **Process (5):** `mcp_used_to_discover_ticket`, `ticket_moved_through_pipeline`, `debug_skill_activated`, `software_planning_skill_activated`, `bugfix_skill_activated`
- **Guard (2):** `no_remote_operations`, `no_test_disabling`

Concrete cleanup from this mapping:
1. Demote the five Process measures from CI-gating to diagnostic — keep them recorded and reportable, don't fail the build on them.
2. Drop `acceptance_artifact_exists` (subsumed by `acceptance_run_report_rendered`).

CI gate after cleanup: 9 Outcome+Quality+Guard measures, ~half the current count.

---

## 5. Adding a new scenario

Procedure for someone writing a new scenario from scratch.

1. **Capture done.** Either commit a known-good artifact / snapshot / reference under `evaluations/fixtures/`, or describe it precisely in a ticket / spec that the agent will read. The fixture must be fully reproducible (seeders for any DB state).

2. **Enumerate verifiers.** For each property of the end state that matters, write down the verifier and pick its tier (Outcome / Quality / Process / Guard). Drop any property without a verifier.

3. **Apply the five lenses to each measure.** Specific, Actionable, Relevant, Time-boxed, Reproducible. Drop anything that fails a lens. Especially be ruthless on Process measures — they're diagnostic, not goals.

4. **Write the scenario YAML.** Generic supervisor prompt; project-specific content in the seeded artifact (ticket description, fixture files), not in the prompt. Set `runs_per_prompt: 5` to start. Time-box every command.

5. **Sensitivity check.** Run the eval against the agent. With the seeded "broken" state, the relevant Outcome/Quality measures should fire. If they don't, the bug is too easy to reach by accident — make the scenario more representative.

6. **Specificity check.** Hand-complete the work in a copy of the fixture. Run the workspace-state measures against it via a script in the shape of `scripts/check_measures_specificity.py`. Every measure must pass for the right reason. Bugs found here go in the codebase, not in the eval.

7. **Merge only after 5 + 6 are green.** Then iterate via the intervention hierarchy in `evaluations/AGENTS.md` (extend skill → new skill → system prompt last).

---

## 6. Maintenance

- **Per-measure ROI.** Track how often each measure has changed a team decision (an intervention, a revert, a follow-up issue). Measures that have driven zero decisions over a long horizon are pruning candidates.

- **Scenario decay.** When the agent or the codebase changes shape, scenarios can become trivial (always pass) or mis-aimed (never pass for unrelated reasons). Trivial scenarios fail the Actionable lens; remove or harden. Mis-aimed scenarios are bugs in the eval, not the agent.

- **Process metric audits.** Quarterly, check whether each Process measure has any cross-tab signal vs Outcome. If not, retire it. Keep only the diagnostic levers that actually help debug regressions.

- **Sensitivity rerun.** When the underlying skill or agent changes, redo the sensitivity test (does the bad path still produce the bad result?). Without it, a measure may silently become non-discriminating.

- **Specificity rerun.** When you patch a measure, rerun the specificity harness against the solution workspace.

---

## Cross-references

- [`evaluations/AGENTS.md`](../evaluations/AGENTS.md) — operator guide: how to run, inspect, fixtures, intervention hierarchy.
- [`scripts/check_measures_specificity.py`](../scripts/check_measures_specificity.py) — the specificity-validation harness pattern, applied to the autopilot fitness scenario.
- [`evaluations/measures.py`](../evaluations/measures.py) — measure implementations, organized by category.
- [`evaluations/scenarios/autopilot_fitness_bug.yml`](../evaluations/scenarios/autopilot_fitness_bug.yml) — the worked-example scenario.
