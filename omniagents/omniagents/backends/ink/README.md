# React Ink TUI Backend

A modern, maintainable terminal user interface for OmniAgents built with React Ink and TypeScript.

## Overview

The Ink backend provides a full-featured terminal interface using React components and TypeScript, making it easier to develop and maintain compared to the Go-based Bubble Tea backend.

## Features

- **React Components**: Built with familiar React patterns (components, hooks, state management)
- **TypeScript**: Full type safety and IDE support
- **Prebuilt in Wheels**: Official release wheels include the built Ink bundle
- **Shared client**: Connects through `@omniagents/client`, the same protocol
  client and conversation layer the desktop app and web UI use
- **Feature Parity**: Supports all features of the Bubble Tea backend

## Usage

### Via CLI

```bash
# From a project directory that contains project.yml
omniagents run --mode ink

# Or explicitly
omniagents run -P path/to/project.yml --mode ink

# Bubble Tea alias (Go TUI)
omniagents run -P path/to/project.yml --mode tui
```

### Development

The TUI is an npm workspace of the omni-mono repository; install once at the
monorepo root.

```bash
# From the monorepo root
npm ci
npm run build -w omni-ink-tui   # → omniagents/backends/ink/tui/dist/index.js
npm run dev -w omni-ink-tui     # development mode with hot reload
```

## Requirements

- Node.js 22 or newer on `PATH`: the TUI runs as `node dist/index.js` and
  uses Node's built-in WebSocket. Official wheels include the built bundle.
- From a source checkout, build the bundle as above; the backend reports how
  when it is missing.

## Architecture

```
omniagents/backends/ink/
├── __init__.py              # Python backend class
├── tui/                     # TypeScript/React TUI
│   ├── src/
│   │   ├── index.tsx        # Entry point
│   │   ├── components/      # React components
│   │   ├── hooks/           # Custom React hooks
│   │   ├── store/           # Terminal view state, mirrored from the shared session
│   │   └── rpc/             # Host helpers and the integration-test fixtures
│   ├── package.json
│   └── tsconfig.json
└── README.md
```

## Theming

The TUI's colors are a theme: an [Omarchy](https://github.com/basecamp/omarchy)
`colors.toml` palette mapped onto the design system's semantic roles. All
22 Omarchy themes ship bundled (vendored by `tui/scripts/import-omarchy-themes.mjs`).

- `/theme` opens the picker: type to filter, arrows preview a swatch strip,
  Enter applies and remembers the choice in `<product config dir>/ink.json`.
- On an Omarchy desktop the TUI follows the desktop's current theme by default
  and retints live when `omarchy-theme-set` runs, the way Omarchy retints
  Claude Code and Pi. Pick "Omarchy" in the picker to go back to following it.
- Your own themes go in `<product config dir>/themes/<name>.toml` (or
  `<name>/colors.toml`) in Omarchy's format. A user theme with a bundled
  theme's id replaces it.
- Precedence: `--theme` flag, saved choice, `OMNI_THEME`, Omarchy's current
  theme, `tokyo-night`.

## Environment Variables

- `OMNI_THEME` — theme id to use when nothing is saved (see Theming)

- `OMNI_DEBUG`: Enable debug logging
- `OMNI_INK_TUI_PATH`: Full path to a prebuilt `dist/index.js` to run instead
  of the packaged one

## Packaging

- `make package-build` installs the workspace at the monorepo root and builds
  the bundle; wheels include `omniagents/backends/ink/tui/dist/` only.

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

1. Edit files in `tui/src/` (shared client behavior lives in `packages/client`)
2. Test with `npm run dev -w omni-ink-tui`
3. Build with `npm run build -w omni-ink-tui`
4. Run type checking with `npm run type-check -w omni-ink-tui`
5. Format code with `npm run format -w omni-ink-tui`

## Protocol

The Ink backend uses the same JSON-RPC 2.0 WebSocket protocol as all other OmniAgents backends. See `/omniagents/rpc/protocol.md` for details.
