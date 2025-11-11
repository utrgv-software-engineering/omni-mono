# Installation

## Requirements

- Python 3.11 or higher
- pip package manager

## Install from Source

Currently, OmniAgents is available via source installation:

```bash
git clone https://github.com/yourusername/omniagents.git
cd omniagents
pip install -r requirements.txt
```

## Install with Optional Dependencies

OmniAgents has several optional dependency groups for different features:

### All Features

```bash
pip install -e ".[all]"
```

### Specific Features

```bash
# GUI/Flet interface
pip install -e ".[flet]"

# Server mode
pip install -e ".[server]"

# Documentation tools
pip install -e ".[docs]"

# Development dependencies
pip install -e ".[dev]"
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

## Prebuilt Assets

Release wheels include prebuilt TUI assets, so end users do not need Go or Node:

- **Bubble Tea TUI**: Native `omni-tui` executable for macOS, Linux, and Windows
- **Ink TUI**: Prebuilt `dist/index.js` bundle

When running from source or editable installs, the backends can auto-build missing clients if the respective toolchain (Go or Node) is available.

## Verify Installation

Verify the installation by running:

```bash
omniagents --version
```

## Next Steps

- [Quick Start](quickstart.md) - Create your first agent
- [Configuration](configuration.md) - Set up API keys and configuration
