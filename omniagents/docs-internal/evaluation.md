# Evaluation (dbt‑style)

- Define measures with `@omniagents.core.evaluation.evaluation_measure`.
- Place project measures in `evaluations/measures.py` (auto‑loaded on compile).
- Run evaluations via the `omniagents` CLI (compiles and executes scenarios).

Basic measure example

```python
from omniagents.core.evaluation import evaluation_measure, build_eval_context, run_measures

@evaluation_measure
def my_measure(ctx):
    return {"passed": True, "score": 1.0}

ctx = build_eval_context(history=[{"role":"user","content":"hi"}], tools=[])
print(run_measures(ctx))
```

Built‑in measures

- `tool_hallucination`: flags tool calls to unknown tools and provides counts.
- Custom measures must register with unique names. Attempting to reuse a name raises an error, and measures that return non-dict payloads or reference unknown names during execution also fail immediately.

Project layout

- `project.yml`: declares paths (`evaluations/`, `target/`, etc.) and agent file.
Everything lives in `evaluations/` (or `paths.evaluations`), one place per
kind of thing (`omniagents/core/eval/layout.py`). What people write:

- `evaluation.yml`: defaults, environment, `synthetic_data_generation`, `failure_categories`, measure descriptions, `metrics` (see below).
- `measures.py`: project‑specific measures (auto‑loaded).
- `judges/<key>/judge.yml`: an LLM‑as‑Judge, the file being the judge's settings, beside its `instructions.md` and `template.md`.
- `optimizers/<key>/optimizer.yml`: an optimizer, the same way.
- `scenarios/**/*.yml`: the scenarios a suite runs (a `scenarios:` list or a bare list; entries may include other files).

Produced by people and tools, and committed: everything expensive or
impossible to make again, and everything a reported number depends on.

- `reviews/<run id>.jsonl`: people's verdicts on a run's answers, made in Studio's Logs. One line per reviewed conversation: the verdict (`acceptable`/`unacceptable`), failure categories seen, notes, reviewer and time, with the scenario and the answer as reviewed (`reply`, and the whole conversation as `history` when a judge of the project reads conversations), so it stands on its own after the run is gone.
- `generated/<set>/`: a generated scenario set (`scenarios.yml`, `manifest.json`). Regenerating costs model calls and never gives the same set.
- `holdout/`: the suite's train/dev/test assignment (`manifest.yml` and one file of names per split). Fixed, so a test split stays a test split.
- `judge-datasets/<category>/`: a category's judge dataset, built from every reviewed answer (`train.yml`, `dev.yml`, `test.yml`, `manifest.yml`). An answer is positive when reviewers saw the category in it; its split depends only on it and the seed, so rebuilding after more reviews never moves an earlier example and a judge's test examples stay out of its training.
- `baselines/<name>/`: a run kept as evidence for a number someone reports, with the agent it tested (`omniagents eval baseline <run id> --name <name>`, or Studio).

Local results (the scaffolded `.gitignore` ignores them):

- `runs/<run id>/`: each saved run, whatever it ran: `results.json`, `metadata.json`, and `agent/` with the agent's `agent.yml` and instructions as they were. `runs/index.json` lists them, newest first.
- `optimizations/<id>_<agent|judge>_<subject>/`: each optimization's log, result and override. Applied results land in the agent's or judge's files.

Reviews and generated sets hold agents' answers, so **evaluation inputs must be
synthetic**: never put real patient or personal data in a scenario. A review of
a conversation that is not an evaluation answer (Playground, desktop) stays in
the machine's trace database and is never committed.

Nothing is read from anywhere else: an evaluations directory with files in the
old places (`scenarios.yml`, `metrics.yml`, `splits/`, `results/`, judges
inline in `evaluation.yml`, `artifacts/eval/` in the project, judge datasets
built from scenario labels) is refused with what to move where.

Running evaluations

```bash
# Compile (optional; run compiles automatically)
python -m omniagents.cli compile -P path/to/project.yml

# Run scenarios and compute metrics; the run is saved in evaluations/runs/
python -m omniagents.cli eval suite run -P path/to/project.yml --reporter pretty
```

Programs use `omniagents.core.eval.workspace.EvalWorkspace(project)` rather than
reading these files themselves: it edits dimensions, failure categories,
measures, scenario sets, judges, judge datasets and the holdout in place, runs
the suite or a scenario set, and lists, reads and compares saved runs. The CLI
and Studio both go through it.

Environment config (declarative)

The `evaluation.environment` block declares the eval environment as
data: a `type` naming a registered environment factory plus that
factory's knobs. There is no class-path form — products keep no
environment Python. Relative paths resolve against the `evaluations/`
directory.

```yaml
evaluation:
  environment:
    type: local_workspace              # built-in factory

    # LocalWorkspaceEnvironment knobs (all optional)
    fixtures_dir: fixtures/workspaces  # fixture workspace tree
    workspace_root: ~/.cache/my_product/eval-workspaces
    skills_subdir: .my_product/skills
    keep_tempdir_env: MY_EVAL_KEEP_TEMPDIR
    tempdir_prefix: my_eval_

    # Static env vars applied around every scenario. ${workspace} is the
    # per-scenario temp dir, ${workspace_root} the configured cache root;
    # unknown ${...} tokens are left as-is.
    env:
      MY_WORKSPACE_ROOT: "${workspace}"
      MISE_TRUSTED_CONFIG_PATHS: "${workspace_root}"

    # Optional: compose ContainerRoutingMixin. `profile_resolver:
    # product` is the installed product's layered resolver
    # (resolve_product_profile); a dotted path imports a custom one.
    # `default_profile` backs the bare `environment.image` shorthand;
    # `profile`/`image` are class-level fallbacks (per-scenario
    # `environment:` blocks win).
    container_routing:
      profile_resolver: product
      default_profile: profiles/devbox.yml

    # Optional: per-scenario user-MCP fixture staging. When a scenario
    # sets `<scenario_key>: <name>`, an isolated XDG_CONFIG_HOME is
    # staged inside the temp dir with a generated
    # `<config_name>/mcp.json` pointing the declared server at
    # `<dbs_dir>/<name>.db` (copied to db_dest, or initialized fresh by
    # the server) and `<dbs_dir>/<name>.pages` (copied to pages_dest).
    # `cli`/`command` accept `{env_var, default}` — the env var
    # overrides the declared default at runtime.
    mcp_fixture:
      scenario_key: db_fixture
      dbs_dir: fixtures/dbs
      config_name: my_product
      server_name: my-mcp-server
      command: { env_var: MY_MCP_NODE, default: node }
      cli: { env_var: MY_MCP_CLI, default: ~/path/to/dist/cli.js }
      args: ["${cli}", "--db-path", "${db_path}", "--pages-dir", "${pages_dir}"]
      db_dest: projects.db
      pages_dest: projects
```

Environments that genuinely aren't expressible as configuration
register a factory from project Python the pipeline already loads
(`evaluations/measures.py` runs before the environment is built):

```python
from omniagents.core.eval.environment_config import register_environment_type

register_environment_type(
    "booking_api", lambda cfg, *, project_root, eval_dir: BookingEnv(**cfg)
)
```

then declare `environment: {type: booking_api, ...}`. See
`examples/eval_with_environment/`.

Metrics config (under `evaluation:` in `evaluation.yml`)

```yaml
metrics:
  - name: unknown_tool_call_rate
    label: Unknown Tool Call Rate
    type: rate
    from_measure: tool_hallucination
    numerator: counts.unknown_tool_calls
    denominator: counts.total_tool_calls

  - name: my_pass_rate
    label: My Pass Rate
    type: pass_rate
    source_measure: my_measure
    target: { op: ">=", value: 0.95 }
```

Scenario pipeline (optional)

```bash
# Generate synthetic scenarios from dimensions into a named set
python -m omniagents.cli eval scenarios generate -P path/to/project.yml --name myset --count 100

# Run the set; each run keeps its conversation for review and for judges
python -m omniagents.cli eval scenarios run -P path/to/project.yml --set myset

# Review the run's answers in Studio's Logs (verdict, categories, notes), then
# build the judge dataset (train/dev/test) for one category from the reviews
python -m omniagents.cli judge dataset -P path/to/project.yml -c <failure_category>

# Evaluate an LLM‑as‑Judge on a dataset split (it judges the reviewed answers)
python -m omniagents.cli judge run -P path/to/project.yml -c <category> -s dev -j <judge_key>
```

Notes

- Selectors: use `--select/--exclude` with expressions like `name:glob`, `tag:glob`, `measure:glob`.
- `--set` takes a generated set's name or a scenario file's path.
- A scenario's `failure_categories` say what it is written to probe. What reviewers saw is in `reviews/`.

## Repeats, intervals, slices and a holdout

An agent's output varies run to run, so a single run is one draw from a pass rate. The pipeline supports the pieces needed to report a rate instead of a verdict, and to compare two runs honestly:

- `evaluation.defaults.runs_per_prompt: N` fans out N runs per scenario; each run record carries `trial` (0-based) and `repeats`.
- Each run carries `answered` - whether a final answer was produced. Unanswered runs count as failures by default; `answered_rate` (a metric type) reports how many there were, and `exclude_nonresponse: true` on a `pass_rate` metric drops them from that metric's denominator.
- Every `pass_rate` carries a 95% Wilson `interval`. `slice_by: [expect.skin_tone, tags]` on a metric, or `evaluation.defaults.slice_by`, reports the rate per value of each field under `slices`; the CLI prints one informational line per slice.
- Each saved run (`runs/<run_id>/results.json`, indexed in `runs/index.json`) includes a `manifest`: model, resolved tools, instruction and agent-file hashes, framework version, `select`/`exclude`/`split`, repeats. Compare two runs by their manifests before comparing their numbers.
- `omniagents eval scenarios holdout --strata expect.skin_tone,expect.malignant --fractions 0.5,0.25,0.25 --seed 42` writes `evaluations/holdout/{train,dev,test}.yml` (scenario names) and `manifest.yml` (seed, strata, counts per stratum). `eval suite run --split test` restricts a run to the split and records it in the manifest. This is an *agent-evaluation* holdout; `omniagents judge dataset` builds a judge's train/dev/test from reviewed answers and is unrelated.

```yaml
# evaluations/evaluation.yml
evaluation:
  metrics:
    - name: top1_accuracy
      type: pass_rate
      source_measure: top1_match
      slice_by: [expect.skin_tone]
      target: { op: ">=", value: 0.30 }
    - name: answered
      type: answered_rate
      target: { op: ">=", value: 0.95 }
```
