# React Ink TUI Backend

A modern, maintainable terminal user interface for OmniAgents built with React Ink and TypeScript.

## Overview

The Ink backend provides a full-featured terminal interface using React components and TypeScript, making it easier to develop and maintain compared to the Go-based Bubble Tea backend.

## Features

- **React Components**: Built with familiar React patterns (components, hooks, state management)
- **TypeScript**: Full type safety and IDE support
- **Auto-build**: Automatically builds on first run if Node.js is installed
- **Same Protocol**: Uses the same JSON-RPC WebSocket protocol as other backends
- **Feature Parity**: Supports all features of the Bubble Tea backend

## Usage

### Via CLI

```bash
# Using the ink backend directly
python -m omniagents.cli run -c path/to/agent.yaml --mode ink

# Using the tui alias (still uses Bubble Tea by default)
python -m omniagents.cli run -c path/to/agent.yaml --mode tui
```

### Development

```bash
# Navigate to the TUI directory
cd omniagents/backends/ink/tui

# Install dependencies
npm install

# Build the TypeScript code
npm run build

# Run in development mode with hot reload
npm run dev
```

## Requirements

- Node.js >= 18.0.0
- npm (comes with Node.js)

If Node.js is not installed, the backend will display installation instructions.

## Architecture

```
omniagents/backends/ink/
├── __init__.py              # Python backend class
├── tui/                     # TypeScript/React TUI
│   ├── src/
│   │   ├── index.tsx        # Entry point
│   │   ├── components/      # React components
│   │   ├── hooks/           # Custom React hooks
│   │   ├── rpc/             # JSON-RPC client
│   │   └── types/           # TypeScript types
│   ├── package.json
│   └── tsconfig.json
└── README.md
```

## Environment Variables

- `OMNI_INK_AUTO_BUILD`: Set to "0" or "false" to disable auto-build
- `OMNI_DEBUG`: Enable debug logging
- `OMNI_INK_TUI_PATH`: Full path to a prebuilt `dist/index.js` to skip build

## Comparison with Bubble Tea Backend

| Feature | Bubble Tea (Go) | Ink (TypeScript) |
|---------|----------------|------------------|
| Language | Go | TypeScript/JavaScript |
| Lines of Code | ~6,600 | ~2,700 (59% reduction) |
| Component Model | Bubble Tea | React |
| Type System | Go types | TypeScript |
| Build Tool | go build | npm/esbuild |
| Hot Reload | No | Yes (dev mode) |
| Ecosystem | Go modules | npm packages |

## Contributing

When making changes to the Ink TUI:

1. Edit files in `tui/src/`
2. Test with `npm run dev`
3. Build with `npm run build`
4. Run type checking with `npm run type-check`
5. Format code with `npm run format`

## Protocol

The Ink backend uses the same JSON-RPC 2.0 WebSocket protocol as all other OmniAgents backends. See `/omniagents/rpc/protocol.md` for details.

## Build Details

- Auto-build runs in a per-user cache directory (e.g., `~/.cache/omniagents/ink/tui` on Linux) to avoid write-permission issues with system installs and pipx.
- If a lockfile (`package-lock.json`) is present, `npm ci` is used; otherwise `npm install`.
- Set `OMNI_INK_TUI_PATH` to point to a prebuilt `dist/index.js` to bypass local builds entirely.
