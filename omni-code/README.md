# Omni Code

A standalone coding agent built with OmniAgents. It gives an AI assistant safe access to your local workspace and a rich set of tools to search, edit, and run code; fetch docs; and summarize content.

## Features
- Rich workspace tools: list, search, read, edit, and patch files; run shell commands
- Web and docs tools: fetch web pages, Google/YouTube/Scholar via SerpAPI, download files, convert many formats to Markdown
- Optional safe-guarded execution with approvals
- Simple CLI-based runner

## End-User Install
- Python 3.10+, Go 1.20+, and libgit2 must be available on your PATH.
- Choose either pipx or uv to install the `omni` CLI globally, then run `omni-setup` to configure credentials.

### Option A: pipx (recommended)
```bash
python3 -m pip install --user pipx
pipx ensurepath
pipx install omni-code --pip-args="--extra-index-url https://pypi.fury.io/ericmichael/"
omni-setup
omni
```

Windows (PowerShell):
```powershell
py -m pip install --user pipx
pipx ensurepath
pipx install omni-code --pip-args="--extra-index-url https://pypi.fury.io/ericmichael/"
omni-setup
omni
```

### Option B: uv tool install
```bash
python3 -m pip install --user uv
uv tool install omni-code --extra-index-url https://pypi.fury.io/ericmichael/
omni-setup
omni
```

Windows (PowerShell):
```powershell
py -m pip install --user uv
uv tool install omni-code --extra-index-url https://pypi.fury.io/ericmichael/
omni-setup
omni
```

The setup wizard guides you through choosing your provider (OpenAI, Azure, or custom), securely collects required keys, and writes them to a global config file:
- macOS/Linux: `~/.config/omni-code/config.env`
- Windows: `%APPDATA%\OmniCode\config.env`

You can rerun `omni-setup` anytime to update credentials. After setup, run `omni` from any directory to start the assistant.

### Using Azure OpenAI
- Run `omni-setup` and choose option 2 (Azure) to provide:
  - `AZURE_OPENAI_API_KEY`
  - `AZURE_OPENAI_API_VERSION`
  - `AZURE_OPENAI_ENDPOINT` (for example `https://your-resource.openai.azure.com/`)
  - `AZURE_OPENAI_DEPLOYMENT`
- Optional telemetry fields (`OPENAI_ORG`, `OPENAI_PROJECT`) are prompted when present in your existing config.
- Launch `omni`; the runner detects `OPENAI_PROVIDER=azure` from your saved config and uses the Azure client automatically.
- Advanced users can export the same variables manually instead of using the wizard.

## Developer Setup

### Project layout
```
.
├── main.py
├── tools/
├── oagents/
├── server_functions/
├── omni_code/
├── tests/
├── docs/
├── pyproject.toml
└── requirements.txt
```

### Prerequisites
- Python 3.10+
- Go 1.20+
- libgit2 (e.g., `brew install libgit2`, `sudo apt-get install -y libgit2-dev`)
- Run `omni-setup` once to capture credentials in your global config if you have not already.
- For Azure OpenAI development, choose the Azure option inside the setup wizard to populate the required variables.

### Installing Go
- Ubuntu/WSL2:
  ```bash
  sudo add-apt-repository ppa:longsleep/golang-backports
  sudo apt update
  sudo apt install -y golang-go
  go version
  ```
- macOS:
  ```bash
  brew install go
  go version
  ```
- Windows:
  Install via winget (`winget install -e --id GoLang.Go`) and verify with `go version`.

### Local development workflow
```bash
git clone git@github.com:<you>/omni-code.git
cd omni-code
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev] --extra-index-url https://pypi.fury.io/ericmichael/
omni-setup
omni
```

Run `omni-setup` again anytime you need to update credentials. To use the CLI globally without activating the virtual environment, follow either the pipx or uv instructions above.

### Running tests
```bash
pytest
```

### Packaging and release
- Versioning, building, and publishing mirror the OmniAgents process.
- Run `make bump-patch`/`bump-minor`/`bump-major` to adjust the version.
- `make publish` builds distributions and uploads them to Fury using `FURY_USER` and `FURY_TOKEN` from `.env`.
- See `docs/RELEASE.md` for the full workflow.

### Installing via requirements.txt
```bash
pip install -r requirements.txt
```
The file includes the private Gemfury index URL required for `omniagents` and `agentio`.

## License
MIT (see LICENSE)
