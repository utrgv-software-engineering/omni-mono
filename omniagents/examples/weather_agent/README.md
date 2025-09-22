# Weather Agent

Example agent that geocodes a city name and fetches current weather using Open‑Meteo (no API key required).

Only two tools are exposed to encourage chaining: `geocode` then `get_weather`.

## Usage (Python)

```bash
python examples/weather_agent/agent.py --mode cli
# or GUI
python examples/weather_agent/agent.py --mode gui
# or JSON‑RPC server
python examples/weather_agent/agent.py --mode server -p 8000
```

## Usage (YAML)

```bash
python -m omniagents.cli run -c examples/weather_agent/agent.yaml --mode cli
```

Ask things like: "Weather in Paris" or "What’s the weather in New York?"

## Evaluation Demo

Run all evaluation scenarios defined in the YAML and compute metrics:

```bash
export OPENAI_API_KEY=...  # required for the model to run
python examples/weather_agent/evaluate_yaml.py --config examples/weather_agent/agent.yaml -d
```

This runs multiple prompts from the YAML, loads each recorded history, and reports metrics like:
- Happy Path Pass Rate
- Ambiguity Handling Pass Rate
- Fahrenheit Temperature Pass Rate
- Unknown Tool Call Rate
- Friendliness Pass Rate
