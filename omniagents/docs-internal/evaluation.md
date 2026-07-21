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
