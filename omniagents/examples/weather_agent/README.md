# Weather Agent

Example agent that geocodes a city name and fetches current weather using Open‑Meteo (no API key required).

Only two tools are exposed to encourage chaining: `geocode` then `get_weather`.

## Usage (Python)

```bash
python examples/weather_agent/agent.py
# or terminal interface
python examples/weather_agent/agent.py --mode ink
# or JSON‑RPC server
python examples/weather_agent/agent.py --mode server -p 8000
```

## Usage (YAML)

```bash
python -m omniagents.cli run -P examples/weather_agent/project.yml
```

Ask things like: "Weather in Paris" or "What’s the weather in New York?"

## Evaluation (dbt-style)

Organize evaluation assets under `evaluations/` and use the omniagents CLI (dbt-style):

```bash
# Compile (optional; most eval commands compile automatically)
python -m omniagents.cli compile -P examples/weather_agent/project.yml

# Run evaluation
export OPENAI_API_KEY=...  # required for model runs
python -m omniagents.cli eval run -P examples/weather_agent/project.yml --reporter pretty
```

This compiles resources into `target/evaluation_compiled.yml` and evaluates with the runner. Reports metrics like:
- Happy Path Pass Rate
- Ambiguity Handling Pass Rate
- Fahrenheit Temperature Pass Rate
- Unknown Tool Call Rate
- Friendliness Pass Rate

To generate synthetic scenarios into a named set (outputs under `artifacts/eval/<name>/`), run:

```bash
python -m omniagents.cli eval scenarios generate -P examples/weather_agent/project.yml --name demo --count 100
```

After running scenarios to produce traces in Studio, annotate all scenarios with failure categories (keeps all scenarios, does not prune). The output goes to the same `<name>` folder as the input:

```bash
python -m omniagents.cli eval annotate -P examples/weather_agent/project.yml \
  --name demo \
  -o artifacts/eval/annotated_scenarios.yml

To create train/dev/test splits for a failure category (writes under `artifacts/eval/<name>/splits/<category>/`):

```bash
python -m omniagents.cli eval split \
  --name demo \
  -c out_of_scope_engagement
```

Use `--name` consistently to reference the scenario set.

Evaluate an LLM-as-Judge on a split and report TPR/TNR (requires `OPENAI_API_KEY`):

```bash
export OPENAI_API_KEY=...
python -m omniagents.cli eval judge run \
  -P examples/weather_agent/project.yml \
  --name demo -c out_of_scope_engagement -s dev \
  -j out_of_scope_judge --max-turns 1 --run-max-turns 6
```
```
