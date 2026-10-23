# Evaluations — Workflow & Methodology

This directory holds the evaluation suite for omni-code. Evals exist to
**measure agent behavior so we can iterate on prompts and skills with
real signal**. This file is the operator's guide for running and
inspecting evals in this project.

---

## Running an eval

```bash
python -m omniagents.cli eval suite run -P omni_code/project.yml \
  --select 'name:<scenario_name>' --reporter pretty
```

Every run is saved in this directory's `runs/` (`-o` only copies the
`results.json` somewhere else as well):

```
evaluations/runs/<run id>/results.json    # every run record, summary, metrics, manifest
evaluations/runs/<run id>/metadata.json   # which scenarios, agent, source
evaluations/runs/<run id>/agent/          # agent.yml and instructions as they were
evaluations/runs/index.json               # every run, newest first
```

`runs/` is local (gitignored); keep a run that backs a number you report
with `omniagents eval baseline <run id> --name <name>` (committed, in
`baselines/`). Reviews made in Studio's Logs (`reviews/`), generated sets,
the holdout and judge datasets are committed too. Where everything in this
directory goes is `omniagents/core/eval/layout.py`.

**Scenarios must be synthetic.** Reviews and generated sets hold the agent's
answers and are committed, so never put real patient or personal data in a
scenario, fixture or prompt.

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
jq -r '.runs[0].transcript_path' evaluations/runs/<run id>/results.json
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

## One engine runs every scenario

Every evaluation - `eval suite run`, `eval scenarios run`, Studio jobs and
the notebook API (`omniagents.notebook.EvalSuite`) - runs through
`omniagents/core/eval/engine.py`: `run_scenario` (single prompt, scripted
`prompts`/continuations, synthetic) and `run_suite` (repeats, metrics,
manifest). Only the agent target differs (`core/eval/targets.py`: the
project agent, or an `agents.Agent` from a notebook). A change to what a
run records or how it is scored goes there, once.

The notebook can run this folder as-is:

```python
from omniagents.notebook import EvalSuite

suite = EvalSuite.from_project("omni_code/project.yml", select="name:<scenario_name>")
results = await suite.run()          # the project agent, same as `eval suite run`
```

## Don't reinvent — read omniagents source first

The framework records a lot more than its CLI surfaces:

- Session history (full message log including tool calls + outputs):
  `~/.omniagents/sessions/<project>/<agent>/sessions.db`
- Traces / spans: `~/.omniagents/traces/`
- Structured run results: `evaluations/runs/<run id>/`

There's already a `omniagents sessions {db-path,list,search,export}`
CLI. Before adding new recording or inspection plumbing,
`grep -r omniagents/core/eval/` and `omniagents/core/session/` for what
already exists. The gap is usually *discoverability*, not data.

---

## Existing custom measures (in `measures.py`)

Project-specific measures registered in `evaluations/measures.py`.

Outcome measures:
- `command_in_workspace_succeeds` — runs a shell command in the
  workspace, passes on exit 0

Process / workflow measures (cross-tab variables, not gates):
- `<skill_name>_skill_activated` — one per tracked skill
