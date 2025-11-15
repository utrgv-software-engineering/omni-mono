# Installation

## Quick Start (Recommended)

### Requirements

- Python 3.11 or higher
- Node.js and npm (for Ink TUI backend and web interface)

### Install OmniAgents

```bash
pip install --upgrade --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[all]"
```

This installs OmniAgents with all available backends, including web, server, and Ink.

### Install Node.js

If you don't have Node.js installed:

- **macOS**: `brew install node`
- **Linux**: Use your package manager or download from [nodejs.org](https://nodejs.org/)
- **Windows**: Download installer from [nodejs.org](https://nodejs.org/)

That's it! You can now run OmniAgents. See the [Quick Start](quickstart.md) guide to create your first agent.

---

## Advanced Installation

### Install Specific Features Only

If you only need certain features, you can install just what you need:

```bash
# Server mode only
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[server]"

# Studio only
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[studio]"

# Web mode only
pip install --index-url https://pypi.fury.io/ericmichael/ --extra-index-url https://pypi.org/simple "omniagents[web]"
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

- `[all]` - All features (web, server, ink, studio)
- `[web]` - Web interface (includes optional realtime voice controls)
- `[server]` - Server mode with FastAPI
- `[ink]` - Ink TUI mode
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
