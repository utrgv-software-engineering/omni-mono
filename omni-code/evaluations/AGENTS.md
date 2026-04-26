# Evaluations — Workflow & Methodology

This directory holds the evaluation suite for omni-code. Evals exist to
**measure agent behavior so we can iterate on prompts and skills with
real signal**. Read the methodology section before designing experiments.

> **Designing a new scenario or measure?** Read [`docs/EVAL_DESIGN.md`](../docs/EVAL_DESIGN.md)
> first. It covers the quality criteria every measure must pass, the
> end-state-first design procedure, and the Outcome/Quality/Process/Guard
> tier hierarchy. This file is the operator's guide; that file is the
> design framework.

---

## Running an eval

```bash
python -m omniagents.cli eval suite run -P project.yml \
  --select 'name:<scenario_name>' --reporter pretty
```

**Do not pass `-o`.** It writes raw JSON wherever you point and bypasses
the structured results storage. Without `-o`, results land at:

```
artifacts/eval/results/<timestamp>/{results.json,metadata.json}
artifacts/eval/results/runs.json    # index of all runs
```

For multi-run scenarios (see "Multiple runs" below), all N runs land in
the same `results.json` under `runs[]`.

---

## Inspecting a run

Every scenario run automatically writes its full message history to:

```
target/eval_transcripts/<timestamp>-<scenario_name>.json
```

The path is also surfaced in the run's `transcript_path` field inside
`results.json`. The transcript is the **first thing to look at** when
a measure fails or a run looks weird — it has every user prompt, every
tool call (with arguments), every tool output, and every assistant
message in order.

```bash
# Latest transcript for a scenario:
ls -t target/eval_transcripts/*<scenario_name>*.json | head -1

# Or look up directly from the run JSON:
jq -r '.runs[0].transcript_path' artifacts/eval/results/<timestamp>/results.json
```

For deeper inspection (full session export with stable schema):

```bash
python -m omniagents sessions list --agent omni --limit 5
python -m omniagents sessions export <session_id> --agent omni --pretty
```

---

## Multiple runs (non-determinism)

Agents are non-deterministic — a single run is not a baseline. Pin a
sample size on the scenario:

```yaml
- name: my_scenario
  fixture: ...
  runs_per_prompt: 5    # all 5 runs land in one results.json under runs[]
```

This works in both the basic and extended (environment-backed) eval
pipelines. `runs_per_prompt:` on a scenario overrides the global default
in `evaluation.yml::defaults`.

For `db_fixture` scenarios the env serializes itself with a class-level
lock (because `XDG_CONFIG_HOME` is process-global), so multi-run is
safe but sequential. Other scenarios stay parallelizable.

---

## DB fixtures (omni-projects MCP)

Scenarios with `db_fixture: <name>` get an isolated `XDG_CONFIG_HOME` and
a fresh sqlite seeded from `evaluations/fixtures/dbs/<name>.db`. The
launcher's `omni-projects-mcp` cli is auto-resolved (or override with
`OMNI_PROJECTS_MCP_CLI=/path/to/cli.js`).

To rebuild a seed db:

```bash
python evaluations/fixtures/dbs/build_<name>.py
```

The seeders use the real cli over stdio so the fixture stays in sync
with whatever schema the cli expects.

---

## Keeping a tempdir for post-mortem inspection

```bash
OMNI_EVAL_KEEP_TEMPDIR=1 python -m omniagents.cli eval suite run ...
```

Preserves the per-scenario tempdir and prints its path. Use when the
transcript alone isn't enough (e.g. you need to inspect what's left in
the workspace filesystem).

---

# Methodology — read this before designing experiments

The whole point of the eval framework is to give you *measurable* impact
per intervention. Treat each change as a controlled experiment.

## 1. Establish a baseline rate over N runs

A single run is noise, not signal. Run the unmodified scenario `N=5`
times to get a *failure rate per measure*. Without a baseline rate,
"I made a change and it passed" tells you nothing. `N=5` is enough to
distinguish a fundamental fix from baseline noise; if you're tempted to
go to `N=10`, the intervention is probably weak.

## 2. Change exactly one lever per round

If you change the system prompt AND add a skill AND tweak a measure, you
can't tell which one moved the needle. Always isolate. Re-run with the
same `N` to compare rates head-to-head.

Don't pre-stack interventions ("I'll add A and B in case A isn't
enough") — that's the confounding mistake. Propose A, measure A, decide
on B based on data.

## 3. Walk the intervention hierarchy

When an eval surfaces a failure mode, propose interventions in this
strict order. Don't skip levels because the principle "feels universal."

1. **Extend an existing skill?** Read the skill's description and
   contents. Check whether the failure falls within its declared scope.
   If yes, iterate the skill text. If the skill is adjacent but focused
   elsewhere (e.g. `debug` is about diagnosis, not the fix loop), skip
   to step 2.

2. **Create a new skill?** If the failure is workflow-specific but no
   existing skill claims that scope, write a new focused skill. New
   skills should chain naturally with adjacent ones (`debug` → `bugfix`
   is a clean composition). Keep each skill's scope narrow.

3. **System prompt as last resort.** Only refactor a principle into the
   system prompt when (a) it has shown up in 3+ unrelated workflows AND
   (b) the eval suite is green. System-prompt changes are cross-cutting,
   bloat the universal context, and are hard to test in isolation.

The default reflex should be "extend or create a skill," not "add to
the system prompt." Skill proliferation is fine; system prompt bloat is
not.

## 4. Never propose "stronger user input" as the fix

Real users (and tickets) are ambiguous. The framework has to absorb
that. If a failure mode is "the agent didn't follow instruction X,"
the fix is in the agent's instructions (skill or system prompt), never
in the scenario's prompt or the seeded ticket. The eval scenario stays
representative of real-world ambiguity.

## 5. Measure outcomes, not process steps

`bug_caught_by_tests` (does the agent's regression test catch the bug?)
is a robust outcome measure. "Did the agent run pytest before the fix?"
is a brittle process measure — different agents will use different
commands, intermediate steps may legitimately vary, false positives and
negatives multiply.

Process-level signals (skill activation, tool-call counts, ticket
moves) are useful as *cross-tab variables* — they help you understand
*why* the outcome moved or didn't — but they should not be the success
criterion of an experiment.

## 6. Beware small-N correlations

At `N=5` a 2/2 vs 1/3 cross-tab is not a real correlation. Don't make
strong claims like "skill activation drives the outcome" from a single
small sample. If a cross-tab pattern looks striking, run again before
building a hypothesis on it.

## 7. Don't narrow-scope omniagents framework changes

If you're modifying behavior in the eval pipeline (e.g. adding a field
to result dicts, changing how something is loaded), there are **four
scenario runners** that all do similar work and almost always need the
same change:

1. `omniagents/core/eval/pipeline.py::_run_scenario` — basic single-turn
2. `omniagents/core/eval/multi_turn.py::_run_scripted_single_turn`
3. `omniagents/core/eval/multi_turn.py::_run_scripted_multi_turn`
4. `omniagents/core/eval/multi_turn.py::_run_synthetic_scenario`

Before changing one, grep the eval module for the affected primitive
(`load_history`, `consume_stream_events`, `final_assistant_text`,
`run_measures`, etc.) and treat the change as "factor a helper or apply
to all four," not "edit the runner I happen to be looking at."

## 8. Don't reinvent — read omniagents source first

The framework records a lot more than its CLI surfaces:

- Session history (full message log including tool calls + outputs):
  `~/.omniagents/sessions/<project>/<agent>/sessions.db`
- Traces / spans: `~/.omniagents/traces/`
- Structured run results: `<project>/artifacts/eval/results/<ts>/`

There's already a `omniagents sessions {db-path,list,search,export}`
CLI. Before adding new recording or inspection plumbing,
`grep -r omniagents/core/eval/` and `omniagents/core/session/` for what
already exists. The gap is usually *discoverability*, not data.

---

## Existing custom measures (in `measures.py`)

Outcome measures (success criteria for the autopilot scenario):
- `db_row_exists` — generic SQL row check against the env-staged db
- `command_in_workspace_succeeds` — runs a shell command in the
  workspace, passes on exit 0
- `bug_caught_by_tests` — reverts a target file to the pristine fixture
  version, re-runs tests, expects failure (proves the agent's regression
  test actually catches the bug)

Process / workflow measures (cross-tab variables, not gates):
- `mcp_used_to_discover_ticket`, `ticket_moved_through_pipeline`
- `no_remote_operations`, `no_test_disabling`, `scope_respected`
- `<skill_name>_skill_activated` — one per tracked skill

When adding a new measure: prefer **outcome-based** (verify the world
state, not the action sequence). When adding a process measure, frame
it as *observation* (track activation rate / tool counts) rather than
*requirement* (must call X before Y).
