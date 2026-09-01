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
- `evaluations/`
  - `evaluation.yml`: defaults, `synthetic_data_generation`, `failure_categories`.
  - `judges/<judge_key>/judge.yml`: LLM-as-Judge configs; per-judge folders.
  - `judges/<judge_key>/instructions.md`, `judges/<judge_key>/template.md`: judge instructions and input template.
  - `metrics.yml`: aggregate metrics (see below).
  - `scenarios.yml`: scenarios or includes; supports dbt‑style selectors.
  - `measures.py`: project‑specific measures (auto‑loaded).

Running evaluations

```bash
# Compile (optional; run compiles automatically)
python -m omniagents.cli compile -P path/to/project.yml

# Run scenarios and compute metrics
python -m omniagents.cli eval run -P path/to/project.yml --reporter pretty
```

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

Metrics config

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

# Annotate using Studio labels, then split train/dev/test
python -m omniagents.cli eval annotate -P path/to/project.yml --name myset
python -m omniagents.cli eval split -P path/to/project.yml --name myset -c <failure_category>

# Evaluate an LLM‑as‑Judge on a split
python -m omniagents.cli eval judge run -P path/to/project.yml --name myset -c <category> -s dev -j <judge_key>
```

Notes

- Selectors: use `--select/--exclude` with expressions like `name:glob`, `tag:glob`, `measure:glob`.
- Use `--name` to select the scenario set folder under `artifacts/eval/<name>/`. Prefer explicit paths with `-i/--input` as needed.

## Repeats, intervals, slices and a holdout

An agent's output varies run to run, so a single run is one draw from a pass rate. The pipeline supports the pieces needed to report a rate instead of a verdict, and to compare two runs honestly:

- `evaluation.defaults.runs_per_prompt: N` fans out N runs per scenario; each run record carries `trial` (0-based) and `repeats`.
- Each run carries `answered` - whether a final answer was produced. Unanswered runs count as failures by default; `answered_rate` (a metric type) reports how many there were, and `exclude_nonresponse: true` on a `pass_rate` metric drops them from that metric's denominator.
- Every `pass_rate` carries a 95% Wilson `interval`. `slice_by: [expect.skin_tone, tags]` on a metric, or `evaluation.defaults.slice_by`, reports the rate per value of each field under `slices`; the CLI prints one informational line per slice.
- `results/<run_id>/metadata.json` includes a `manifest`: model, resolved tools, instruction and agent-file hashes, framework version, `select`/`exclude`/`split`, repeats. Compare two runs by their manifests before comparing their numbers.
- `omniagents eval scenarios holdout --strata expect.skin_tone,expect.malignant --fractions 0.5,0.25,0.25 --seed 42` writes `evaluations/splits/eval/{train,dev,test}.yml` (scenario names) and `manifest.yml` (seed, strata, counts per stratum). `eval suite run --split test` restricts a run to the split and records it in the manifest. This is an *agent-evaluation* holdout; `eval scenarios split` is the judge-training split by failure-category label and is unrelated.

```yaml
# evaluations/metrics.yml
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
