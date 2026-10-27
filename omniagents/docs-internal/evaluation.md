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
- `judge-datasets/<category>/`: a category's judge dataset, built from every reviewed answer (`train.yml`, `dev.yml`, `test.yml`, `manifest.yml`). An answer is labelled Fail when reviewers saw the category in it, Pass otherwise. Splits are balanced by label (each label is divided by the fractions), and rebuilding with the same seed keeps every earlier example where it was, so a judge's test examples stay out of its training. The manifest warns when there are too few answers of a label (validating a judge wants about 50 of each).
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

    # Optional: run scenarios in a container. `profile_resolver:
    # product` is the installed product's layered resolver
    # (resolve_product_profile); a dotted path imports a custom one.
    # `profile` puts every scenario in that sandbox profile's container;
    # `default_profile` backs the bare `environment.image` shorthand;
    # per-scenario `environment: {profile|image}` blocks win. Relative
    # paths resolve against the evaluation directory. The container is
    # registered as the run's execution environment, as `omni serve`
    # registers a profile's sandbox: the agent's file and shell tools
    # (and its workers) work in it, at /workspace, while the model loop
    # and the checks stay on the host. Checks reach it through
    # `ctx.environment.run_command(ctx.environment_context, cmd)`.
    container_routing:
      profile_resolver: product
      profile: ../sandbox_profile_default.yml

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
# Draft combinations of dimension values for a new set, review them in
# evaluations/generated/myset/combinations.yml (remove the ones real users never
# present, add missing ones), then generate scenarios from exactly those
python -m omniagents.cli eval scenarios combinations -P path/to/project.yml --name myset --count 20
python -m omniagents.cli eval scenarios generate -P path/to/project.yml --name myset --count 100

# Run the set; each run keeps its conversation for review and for judges
python -m omniagents.cli eval scenarios run -P path/to/project.yml --set myset

# Review the run's answers in Studio's Logs (verdict, categories, notes), then
# build the judge dataset (train/dev/test) for one category from the reviews
python -m omniagents.cli judge dataset -P path/to/project.yml -c <failure_category>

# Iterate on the judge against the dev split (it judges the reviewed answers)
python -m omniagents.cli judge run -P path/to/project.yml -c <category> -s dev -j <judge_key>
# Then validate it: once, on the test split
python -m omniagents.cli judge run -P path/to/project.yml -c <category> -s test -j <judge_key>
```

Validating a judge (`omniagents.core.eval.judge_validation`)

- TPR and TNR treat **Pass as the positive class**: TPR is how often the judge says Pass when a person said Pass, TNR how often it says Fail when a person said Fail. Iterate on dev until both are above 90% (80% at least).
- A judge's run on its dataset's **test split** is its validation, recorded in `judges/<key>/validation.yml` (committed): the judge version measured (a hash of its settings, instructions and template), the test split, the counts, TPR and TNR. The test split is measured **once per version**: running it again on an unchanged judge and split is refused. Editing the judge makes a new, unvalidated version. `judge optimize --apply` records the applied judge's test measurement the same way.
- A run reports, per judge, its **raw pass rate** and, for a judge validated as it is now, the **corrected rate** with a 95% bootstrap interval (Rogan-Gladen: `(p_obs + TNR - 1) / (TPR + TNR - 1)`, resampling the test labels and the run's verdicts). An unvalidated judge, or one changed since validation, is reported uncorrected and says so (the run's `judges` list, the CLI summary, Studio's run detail and Judges tab).

- A judge's **few-shot examples** are answers from the train split of its category's dataset, listed as `examples:` (answer ids) in its `judge.yml` (`omniagents judge examples -j <key> [ids]`, or Studio's Judges tab). Each is shown to the judge with the reviewer's notes as its critique and the verdict; an answer without notes cannot be one, and one in dev or test is refused (it would leak into the judge's measurement). Pick a clear Pass, a clear Fail and a borderline case. The examples are part of the judge's version.
- **Pin judge models** to a dated snapshot (`gpt-4.1-2025-04-14`, not `gpt-4.1`): a provider can move an alias, and the judge drifts from its validation. The scaffolder and Studio default to a snapshot; `judge run` and the Judges tab warn about an alias.

Generating scenarios

- A generated set starts as **combinations** of dimension values (`generated/<set>/combinations.yml`, committed), a sample a person reviews: they know which combinations real users present. Generation uses exactly the reviewed combinations.
- Unless `synthetic_data_generation.realism` is `false`, a model rates each generated query's realism 1-5 for the user its values describe (`realism: {min_score: 3, model: ...}`, the generator's model by default). Queries below `min_score` are left out of the set and written to `filtered.yml` beside it with their ratings; the manifest counts both.

Reviewing answers (`omniagents.core.eval.review_assist`, Studio)

The person notices; the assistant organizes. Nothing it produces is a label until a person accepts it. Its working state is local, in `runs/review-assist/`; its model is `review_assistant.model` in `evaluation.yml` (a pinned default otherwise).

- **What to review next**: a sample of unreviewed evaluation answers, about two thirds representatives of clusters of similar answers (size and shape, whether they passed, their scenario's dimension values) and a third random picks. Studio's Logs shows it as the **Sample** filter.
- **Failure modes from notes**: people's free-text notes grouped by the failure they describe, matched to existing categories or proposed as new ones (Studio's Categories tab). Accepting one creates the category if new and labels the reviews it grouped.
- **Find more**: every answer read against a failure mode's definition and the answers already labelled with it; each that may show it becomes a suggestion (Logs' **Suggested** filter and the review panel), favouring recall. Accepting labels the answer (and marks it unacceptable if nobody judged it); a dismissed suggestion is not made again.

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

## Scenarios that start the way production does

A scenario normally gives the suite's agent a `prompt`. Two keys start it
another way:

```yaml
scenarios:
  # Run one project agent directly, with this prompt.
  - name: duplicate_is_caught
    agent: triage
    prompt: "Triage issue #15 on acme/api-service."
    measures: [triage_classification]

  # Replay an event through an automation: the agent it starts gets the
  # automation's instruction and the event, exactly as a real run does
  # (`omniagents.core.automations.Automation.run_input`). No `prompt`.
  - name: finds_last_page_off_by_one
    automation: pr-opened              # automations/pr-opened/automation.md
    event: {action: opened, pull_request: {number: 33}}
    github: pr_pagination.yml          # the scenario's GitHub: its issues and pull requests
    fixture: api_service_pr_pagination
    measures: [review_posted_once, review_finds_defects]
```

Judge templates get the event as `event`. Project agents are built exactly
as the project runtime builds them (agents called by name, `extends:`
bases, instruction fragments, context factories, skill folders).

## Slack and GitHub: the workplace

Agents that work in Slack and on GitHub are evaluated in the workplace
simulator (`omniagents-workplace`), the way they run in production:

```yaml
environment:
  type: workplace                    # provided by omniagents-workplace
  fixtures_dir: fixtures/workspaces  # repositories' content
  github_dir: fixtures/github        # issues and pull requests
```

Every scenario gets a workplace of its own (people, Slack, and its
repository on GitHub, seeded from its `fixture` and its `github:` file) and
a fresh sandbox from the project's sandbox profile with the repository
cloned at `/workspace/<name>` from the workplace's git, as an automation run
gets (`omniagents.integrations.environment`). The agents' MCP servers point
at the scenario's workplace (`SLACK_MCP_URL`, `GITHUB_MCP_URL` and their
tokens, resolved for that scenario only), so they push branches, open pull
requests, review and reply as they would against GitHub and Slack. A
`github:` file names the `repository`, its `default_branch` and `ci: {name,
conclusion}`, and lists `issues` and `pull_requests` (a pull request's head
is the scenario's fixture when it names a `base_fixture` for its base, or a
`diff` applied to the fixture). After the run, `ctx.get_env_data("github")`
holds every repository's branches and its issues and pull requests (comments,
reviews, review comments, files), and `ctx.get_env_data("slack")` every
message. Environments record what a run changed by overriding
`EvalEnvironment.collect(context)`, which runs after the agent's last turn
and before judges and measures.

## Failure reasons on judges

A code check fails with a reason (`fail_reason("wrong_agent")`). A judge can
too: list the reasons in `judge.yml` and put a `reason` in its output.

```yaml
# evaluations/judges/task_compliance/judge.yml
agent:
  output_schema:
    properties:
      reasoning: {type: string}
      answer: {type: string, enum: [Pass, Fail]}
      reason: {type: string, enum: [none, missing_request, unrequested_scope]}
    required: [reasoning, answer, reason]
fail_reasons: [none, missing_request, unrequested_scope]
```

A Fail whose `reason` is not one of `fail_reasons` (or is missing) is
recorded as `unspecified`, keeping what the judge said as `reason_given`; a
Pass carries no reason. `ctx.judge_result(key).to_result()` then fails with
that reason and keeps the judge's `reasoning`. Judges without
`fail_reasons` are unchanged.
