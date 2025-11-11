# CLI Backend

Simple text-based terminal interface for running agents.

## Usage

```bash
omniagents run -P agent.yml --mode cli
```

## Features

- Text-based conversation
- Session persistence
- Minimal dependencies
- Scriptable

## Session Management

Resume a previous session:

```bash
omniagents run -P agent.yml --mode cli --session-id <id>
```

## Use Cases

- Remote servers via SSH
- Automation scripts
- CI/CD pipelines
- Quick testing

## Example

```bash
$ omniagents run -P examples/basic_yaml/agent.yml --mode cli

Agent: Hello! How can I help you today?
You: What time is it?
Agent: Let me check the current time for you.
[Calling tool: get_current_time]
Agent: The current time is 2025-11-11 14:30:45.
You: exit
```

## Keyboard Shortcuts

- `Ctrl+C`: Exit
- `Ctrl+D`: Exit

## Configuration

No special configuration required beyond basic agent setup.
