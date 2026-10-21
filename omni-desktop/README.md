# Omni Code Launcher

A desktop application for working with [Omni Code](https://github.com/utrgv-software-engineering/omni-mono/tree/main/omni-code) agents on Windows, macOS (Apple Silicon) and Linux.

The launcher installs the Omni Code runtime, manages its Python environment, and runs agent chats against your projects' folders and repositories.

## Features

- **One-click install** of the Omni Code runtime (Python + venv managed automatically via `uv`)
- **Agent chats** — focus or tile layouts, with Files, Git, Review, Terminal, and Browser alongside each chat
- **Projects** — group chats around local folders and git repositories
- **Dev console** — built-in terminal with automatic venv activation
- **Auto-updates** — the launcher checks for updates on startup

## Requirements

- Node **22+** for development

## Getting Started

### Download

<!-- TODO: Update links when releases are published -->

Download the latest release from [GitHub Releases](https://github.com/ericmichael/omni-desktop/releases).

### Usage

1. Open the launcher and click **Install** to set up the Omni Code runtime
2. Start a chat, or create a project from a folder or repository and chat inside it

## Development

### Prerequisites

- **Node 22+** and npm

### Quick Start

```sh
git clone https://github.com/utrgv-software-engineering/omni-mono.git
cd omni-mono/omni-desktop
npm i
npm run dev
```

`npm install` automatically:

1. Rebuilds native modules (`node-pty`)
2. Downloads the `uv` binary for your platform

On macOS, you may need to remove the quarantine flag from downloaded binaries:

```sh
xattr -d 'com.apple.quarantine' assets/bin/uv
```

### Build

```sh
npm run build       # Build production bundles
npm run package     # Package into installers (dist/)
```

### Useful Commands

| Command        | Description                                               |
| -------------- | --------------------------------------------------------- |
| `npm run dev`  | Run in development with hot reload                        |
| `npm run lint` | Run all checks (ESLint, Prettier, TypeScript, knip, dpdm) |
| `npm run fix`  | Auto-fix lint + format                                    |
| `npm test`     | Run unit tests                                            |

### Code Signing

Local builds may require you to manually allow them to run on Windows and macOS.

On macOS, remove the quarantine flag if the app is rejected:

```sh
xattr -d 'com.apple.quarantine' /Applications/Omni\ Code.app
```

## License

Apache-2.0
