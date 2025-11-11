# GUI Backend (Flet)

Cross-platform desktop interface built with Flet (Flutter-based).

## Installation

```bash
pip install -e ".[flet]"
```

## Usage

```bash
omniagents run -P agent.yml --mode gui
```

## Features

- Visual interface with tabs and panels
- Settings management in-app
- Tool approval dialogs
- Browser-based rendering
- Local storage persistence

## Interface Components

### Main Window

- **Chat Tab**: Conversation interface
- **Tools Tab**: Tool execution logs (when `layout: tabs` is set)
- **Settings**: API key configuration

### Tool Approval

Visual dialogs showing:
- Tool name and description
- Parameters and values
- Approve/Reject buttons

## Configuration

Settings are persisted in browser local storage, including:
- API keys
- User preferences
- Window state

## Use Cases

- Desktop applications
- End-user interfaces
- Visual demonstrations
- Development and testing

## Example

```bash
omniagents run -P examples/safe_agent_yaml/agent.yml --mode gui
```

Launches a desktop window with the agent interface.

## Platform Support

- macOS
- Linux
- Windows

## Dependencies

Requires Flet 0.27.6 or later (installed with `[flet]` extra).
