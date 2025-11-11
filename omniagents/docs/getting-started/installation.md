# Installation

## Quick Start (Recommended)

### Requirements

- Python 3.11 or higher
- Go 1.19+ (for Bubble Tea TUI backend)
- Node.js and npm (for Ink TUI backend)

### Install OmniAgents

```bash
pip install --upgrade --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[all]"
```

This installs OmniAgents with all features including GUI, Server, and TUI backends.

### Install Go

If you don't have Go installed and want to use the Bubble Tea TUI:

- **macOS**: `brew install go`
- **Linux**: Download from [go.dev/dl](https://go.dev/dl/)
- **Windows**: Download installer from [go.dev/dl](https://go.dev/dl/)

### Install Node.js

If you don't have Node.js installed and want to use the Ink TUI:

- **macOS**: `brew install node`
- **Linux**: Use your package manager or download from [nodejs.org](https://nodejs.org/)
- **Windows**: Download installer from [nodejs.org](https://nodejs.org/)

That's it! You can now run OmniAgents. See the [Quick Start](quickstart.md) guide to create your first agent.

---

## Advanced Installation

### Install Specific Features Only

If you only need certain features, you can install just what you need:

```bash
# GUI/Flet interface only
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[flet]"

# Server mode only
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[server]"

# CLI only (no TUI dependencies)
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[cli]"

# Studio only
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[studio]"
```

### Install from Source

For development or if you want the latest unreleased features:

```bash
git clone https://github.com/utrgv-software-engineering/omniagents.git
cd omniagents
pip install -e ".[all]"
```

### Development Installation

For development with all dependencies:

```bash
make dev
```

Or manually:

```bash
pip install -e ".[dev]"
```

### Available Feature Groups

- `[all]` - All features (GUI, Server, TUI, Studio)
- `[flet]` or `[gui]` - GUI/Flet interface
- `[server]` - Server mode with FastAPI
- `[cli]` - CLI mode (no extra dependencies)
- `[studio]` - Studio web UI
- `[docs]` - Documentation tools (MkDocs)
- `[dev]` - Development dependencies (tests, linting, etc.)

## Verify Installation

Verify the installation by running:

```bash
omniagents --version
```

## Next Steps

- [Quick Start](quickstart.md) - Create your first agent
- [Configuration](configuration.md) - Set up API keys and configuration
