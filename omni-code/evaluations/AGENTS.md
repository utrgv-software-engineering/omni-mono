# Evaluations — Workflow & Methodology

This directory holds the evaluation suite for omni-code. Evals exist to
**measure agent behavior so we can iterate on prompts and skills with
real signal**. This file is the operator's guide for running and
inspecting evals in this project.

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

# Methodology — operating the harness

Two omniagents-framework-specific rules about *operating* the eval
pipeline (not designing within it).

## Don't narrow-scope omniagents framework changes

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

## Don't reinvent — read omniagents source first

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

Project-specific measures registered in `evaluations/measures.py`.

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
