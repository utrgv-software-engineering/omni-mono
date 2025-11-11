# TUI Backend (Bubble Tea)

Rich terminal UI built with Bubble Tea (Go).

## Usage

```bash
omniagents run -P agent.yml --mode tui
```

## Features

- Interactive terminal UI with keyboard navigation
- Message streaming
- Session picker with `--resume`
- Native performance
- Prebuilt binaries in wheels

## Session Picker

Resume previous sessions:

```bash
omniagents run -P agent.yml --mode tui --resume
```

Displays an interactive list of sessions with:
- Session ID
- Creation date
- Last message preview
- Message count

## Keyboard Navigation

- `↑/↓`: Navigate messages
- `Enter`: Submit message
- `Ctrl+C`: Exit
- `Tab`: Cycle focus

## Build Requirements

Official wheels include prebuilt `omni-tui` binaries for:
- macOS (Intel and ARM)
- Linux (x86_64)
- Windows (x86_64)

When running from source, the backend can auto-build if Go 1.21+ is available.

## Use Cases

- Local development
- Terminal enthusiasts
- SSH sessions with rich UI
- Performance-critical scenarios

## Source Code

The Bubble Tea TUI is located at:
```
omniagents/backends/bubbletea/cmd/omni-tui/
```

Built with:
- [Bubble Tea](https://github.com/charmbracelet/bubbletea) - TUI framework
- [Glamour](https://github.com/charmbracelet/glamour) - Markdown rendering
- [Bubbles](https://github.com/charmbracelet/bubbles) - TUI components

## Example

```bash
omniagents run -P examples/basic_yaml/agent.yml --mode tui
```

## Alternative: Ink TUI

React-based TUI (experimental):

```bash
omniagents run -P agent.yml --mode ink
```

Requires Node.js/npm for building from source. Wheels include prebuilt bundle.
