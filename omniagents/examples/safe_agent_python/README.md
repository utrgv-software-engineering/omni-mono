# SafeAgent Python Example

This example is similar to the basic Python demo but runs the agent with the SafeAgent wrapper enabled. Each tool call requires user approval.

## Files

- `agent.py` – builds the agent and runs the app with SafeAgent enabled.
- `tools.py` – simple tools used by the agent.

## Usage

```bash
python agent.py --mode cli       # command line interface with approval prompts
python agent.py --mode gui       # Flet GUI with approval cards
python agent.py --mode server    # JSON-RPC server requiring approvals
```
