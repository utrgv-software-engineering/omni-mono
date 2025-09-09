# Omni Code

A standalone coding agent built with OmniAgents. It gives an AI assistant safe access to your local workspace and a rich set of tools to search, edit, and run code; fetch docs; and summarize content.

## Features
- Rich workspace tools: list, search, read, edit, and patch files; run shell commands
- Web and docs tools: fetch web pages, Google/YouTube/Scholar via SerpAPI, download files, convert many formats to Markdown
- Optional safe-guarded execution with approvals
- Simple CLI-based runner

## Project layout
```
.
├── main.py             # Python entry point and system instructions
├── tools/              # Tool implementations (rich tools for OmniAgents UI)
├── tests/              # Unit tests for tools
├── pyproject.toml      # Package metadata (preferred)
└── requirements.txt    # Alternative installation method
```

## Prerequisites
- Python 3.10+
- Go (Golang) 1.20+ (see Install Go below; quick WSL2/Ubuntu commands provided)
- OpenAI API key: set `OPENAI_API_KEY`
- (Optional) SerpAPI key for search tools: set `SERPAPI_API_KEY`
 - libgit2 (for pygit2):
   - macOS: `brew install libgit2`
   - Ubuntu/Debian: `sudo apt-get install -y libgit2-dev`
   - Fedora: `sudo dnf install libgit2-devel`

You can store keys in a `.env` file in the project root:
```
OPENAI_API_KEY=sk-...
SERPAPI_API_KEY=...
```

## Install Go (Golang)
This project requires Go to be available on your system PATH.

- WSL2/Ubuntu (easy install):
  ```bash
  sudo add-apt-repository ppa:longsleep/golang-backports
  sudo apt update
  sudo apt install -y golang-go
  # Verify
  go version
  ```

  Notes:
  - Run these inside your WSL2 Ubuntu shell (not PowerShell/CMD).
  - Optionally add GOPATH bin to your PATH if you plan to install Go tools:
    ```bash
    echo 'export GOPATH="$HOME/go"' >> ~/.bashrc
    echo 'export PATH="$PATH:$GOPATH/bin"' >> ~/.bashrc
    source ~/.bashrc
    ```

- macOS:
  ```bash
  brew install go
  go version
  ```

- Windows (native):
  - Install via winget: `winget install -e --id GoLang.Go`
  - Or download the MSI from https://go.dev/dl/ and follow the installer
  - Open a new terminal and run `go version` to verify

## Quick start (recommended)
```
# Clone your new repo (after you push it remotely)
# git clone git@github.com:<you>/omni-code.git
cd omni-code
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# If omniagents/agentio are private on Gemfury, set your token in env
export FURY_AUTH=TOKEN

# Option A: use pip with extra index (recommended to avoid committing tokens)
pip install -e . --extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"

# Option B: use env for all pip commands in this shell
# export PIP_EXTRA_INDEX_URL="https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
# pip install -e .

# Run the agent (CLI)
omni-code
# or
python main.py
```

The default runner starts an OmniAgents CLI with safe mode enabled for destructive tools. You can adjust behavior in `main.py`.

## Global installation (no venv activation)
If you prefer a globally available `omni-code` command without activating a virtual environment, install using one of the options below.

Prerequisites: ensure Go is on your PATH (`go version`) and libgit2 is installed (see Prerequisites above). Set your API keys in your shell profile (not just a local `.env`) so the global CLI can read them, for example:

macOS/Linux (`~/.bashrc`, `~/.zshrc`):
```bash
export OPENAI_API_KEY=sk-...
# Optional, for search tools
export SERPAPI_API_KEY=...
```

Windows PowerShell profile:
```powershell
$env:OPENAI_API_KEY = "sk-..."  # add to your profile for persistence
# $env:SERPAPI_API_KEY = "..."
```

### Option A: pipx (recommended)
pipx installs the package into its own isolated environment and exposes the `omni-code` CLI on your PATH.

- Install pipx and ensure PATH is updated:
  - macOS/Linux:
    ```bash
    python3 -m pip install --user pipx
    pipx ensurepath
    exec "$SHELL"  # start a new shell so PATH updates take effect
    ```
  - Windows (PowerShell):
    ```powershell
    py -m pip install --user pipx
    pipx ensurepath
    # Open a new terminal afterward
    ```

- If `omniagents`/`agentio` are hosted on a private Gemfury index, set your token:
  - macOS/Linux: `export FURY_AUTH=YOUR_TOKEN`
  - Windows PowerShell: `$env:FURY_AUTH = "YOUR_TOKEN"`

- Install from the local repo directory:
  - macOS/Linux:
    ```bash
    pipx install . --pip-args="--extra-index-url https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
    ```
  - Windows (PowerShell):
    ```powershell
    pipx install . --pip-args="--extra-index-url https://$env:FURY_AUTH:@pypi.fury.io/ericmichael/"
    ```

- Or install directly from your Git remote (replace <you> and branch/tag as needed):
  ```bash
  pipx install "git+https://github.com/<you>/omni-code.git@main" \
    --pip-args="--extra-index-url https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
  ```

- Run it anywhere:
  ```bash
  omni-code
  ```

Upgrade or remove:
```bash
pipx upgrade omni-code
pipx uninstall omni-code
```

### Option B: uv tool install (fast alternative)
If you use `uv`, you can install the CLI as a global tool. This also isolates dependencies.

```bash
# From the repo directory
export FURY_AUTH=YOUR_TOKEN  # if needed
uv tool install . --extra-index-url https://${FURY_AUTH}:@pypi.fury.io/ericmichael/

# Or from Git
uv tool install "git+https://github.com/<you>/omni-code.git@main" \
  --extra-index-url https://${FURY_AUTH}:@pypi.fury.io/ericmichael/

# Run
omni-code
```

### Option C: pip --user (simple, less isolated)
Installs into your user site-packages and places the script in `~/.local/bin` (macOS/Linux) or your AppData Scripts directory (Windows). Ensure that directory is on your PATH.

- macOS/Linux:
  ```bash
  export FURY_AUTH=YOUR_TOKEN
  python3 -m pip install --user . --extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
  # Ensure ~/.local/bin is on PATH
  omni-code
  ```

- Windows (PowerShell):
  ```powershell
  $env:FURY_AUTH = "YOUR_TOKEN"
  py -m pip install --user . --extra-index-url "https://$env:FURY_AUTH:@pypi.fury.io/ericmichael/"
  omni-code
  ```

## Running tests
```
# With Gemfury private index
export FURY_AUTH=TOKEN
pip install -e .[dev] --extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
pytest
```

## Packaging notes
This app depends on the OmniAgents framework:
- `omniagents` and `agentio` can be installed from a private Gemfury index
- Use `--extra-index-url https://${FURY_AUTH}:@pypi.fury.io/ericmichael/` when installing
- Tools are decorated with `@rich_function_tool` and return `RichToolOutput` for a pleasant UI

### Installing via requirements.txt
If you prefer using `requirements.txt`, you can do:
```
export FURY_AUTH=TOKEN
pip install -r requirements.txt --extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
```

Note: pip does not expand environment variables inside requirements.txt lines; pass the index URL via CLI or configure a user-level pip.conf:
```
# macOS/Linux: ~/.config/pip/pip.conf (or ~/.pip/pip.conf)
[global]
extra-index-url = https://${FURY_AUTH}:@pypi.fury.io/ericmichael/
```

 

## License
MIT (see LICENSE)
