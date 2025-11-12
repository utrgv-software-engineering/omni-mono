# SafeAgent Python Example

This example is similar to the basic Python demo but runs the agent with the SafeAgent wrapper enabled. Each tool call requires user approval.

## Files

- `agent.py` – builds the agent and runs the app with SafeAgent enabled.
- `tools.py` – simple tools used by the agent.

## Usage

```bash
python agent.py                  # web interface with approval dialogs (default)
python agent.py --mode server    # JSON-RPC server requiring approvals
python agent.py --mode ink       # terminal interface with approval prompts
```
