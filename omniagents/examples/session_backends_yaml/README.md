# Session Backends (YAML)

Demonstrates the optional `session.backend` YAML block, which delegates
model-context history to one of the SDK's pluggable session backends instead
of OmniAgents' default multi-tenant SQLite store.

See `agent.yml` for the full schema. Supported `type` values:

| `type`                 | Pip extra                       |
| ---------------------- | ------------------------------- |
| `sqlite` (default)     | (built-in)                      |
| `openai_conversations` | (built-in)                      |
| `redis`                | `omniagents[session-redis]`     |
| `sqlalchemy`           | `omniagents[session-sqlalchemy]`|
| `mongodb`              | `omniagents[session-mongodb]`   |
| `encrypted` (wrapper)  | `omniagents[session-encrypted]` |

Run:

```bash
python -m omniagents.cli run -c examples/session_backends_yaml/agent.yml
```

> **Note:** As of the SDK 0.16 upgrade, OmniAgents still feeds full session
> history into `Runner.run_streamed` as `input`. The `session.backend` block
> is parsed and the resolved `Session` instance is stored on
> `AgentSpec.session_backend`, but the runner-level wiring (passing it as
> `Runner.run_streamed(session=...)`) is deferred until manual history feed
> is replaced. See `omniagents/core/session/backends.py` for details.
