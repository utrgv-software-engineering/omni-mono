# SafeAgent YAML Example

This configuration uses the SafeAgent wrapper so that every tool call requires user approval.

## Usage

Run the agent with the OmniAgents CLI:

```bash
python -m omniagents.cli run -c agent.yml
```

You can also use `--mode server` for a JSON-RPC server, or `--mode ink` for a terminal interface.
