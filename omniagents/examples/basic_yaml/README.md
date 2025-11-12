# Basic YAML Example

This example defines the agent in YAML. The `tools` directory contains Python functions that are automatically discovered.

## Usage

Run the agent with the OmniAgents CLI:

```bash
python -m omniagents.cli run -c agent.yml
```

You can also use `--mode server` for a JSON-RPC server, or `--mode ink` for a terminal interface.
