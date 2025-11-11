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
