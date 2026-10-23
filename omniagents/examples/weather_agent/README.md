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

## Evaluation

The evaluation lives in `evaluations/`: settings and metrics in
`evaluation.yml`, the suite in `scenarios/`, a judge in
`judges/ambiguous_location_assumption_judge/`, two generated sets in
`generated/`, and the judge dataset for `ambiguous_location_assumption` in
`judge-datasets/`, drawn from `generated/in_class_error_analysis/`.

```bash
export OPENAI_API_KEY=...  # required for model runs

# Run the suite; the run is saved in evaluations/runs/
python -m omniagents.cli eval suite run -P examples/weather_agent/project.yml --reporter pretty

# Generate a set from the dimensions into evaluations/generated/demo/, and run it
python -m omniagents.cli eval scenarios generate -P examples/weather_agent/project.yml --name demo --count 100
python -m omniagents.cli eval scenarios run -P examples/weather_agent/project.yml --set demo

# After reviewing that run's conversations in Studio, label the set with the
# failure categories reviewers gave, and build a category's judge dataset
python -m omniagents.cli eval scenarios annotate -P examples/weather_agent/project.yml --set demo
python -m omniagents.cli judge dataset -P examples/weather_agent/project.yml \
  --set demo -c ambiguous_location_assumption

# Check the judge on the dataset's dev split (it judges the saved conversations)
python -m omniagents.cli judge run -P examples/weather_agent/project.yml \
  -c ambiguous_location_assumption -s dev -j ambiguous_location_assumption_judge
```
