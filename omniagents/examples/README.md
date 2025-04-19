# OmniAgents Examples

This directory contains demo scripts and agent configurations showing how to use the `omniagents` package.

## Prerequisites

- Python ≥ 3.11
- uv CLI installed (https://astral.sh/uv)

## Running the IBJJF Assistant

1. Change into this folder:
   ```bash
   cd examples
   ```
2. Launch the Flet app for the IBJJF Assistant:
   ```bash
   uv run -m omniagents.cli run -c agents/ibjjf.yaml
   ```

This command will:
- Create an isolated environment
- Install your local `omniagents` package (editable) and example dependencies from `pyproject.toml`
- Start the interactive UI

## Running Other Agents

Any other YAML in `agents/` can be used in the same way:

```bash
uv run -m omniagents.cli run -c agents/<your-agent>.yaml
```

## Cleanup

- Generated files like `.venv/`, `main.py`, or `.python-version` are safe to delete or ignore if you don't need them.

Happy experimenting with OmniAgents!
