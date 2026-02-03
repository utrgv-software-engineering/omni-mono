# Omni Code

A standalone coding agent built with OmniAgents. It gives an AI assistant safe access to your local workspace and a rich set of tools to search, edit, and run code; fetch docs; and summarize content.

## Features
- Rich workspace tools: list, search, read, edit, and patch files; run shell commands
- Web and docs tools: fetch web pages, Google/YouTube/Scholar via SerpAPI, download files, convert many formats to Markdown
- Optional safe-guarded execution with approvals
- Ink TUI and web runner (default mode: `ink`)

## End-User Install
- Python 3.10+, Go 1.20+, and libgit2 must be available on your PATH.
- Choose either pipx or uv to install the `omni` CLI globally, then run `omni model setup` to configure credentials.

### Option A: pipx (recommended)
```bash
python3 -m pip install --user pipx
pipx ensurepath
pipx install omni-code --pip-args="--extra-index-url https://pypi.fury.io/ericmichael/"
omni model setup
omni
```

> Note: Keep pip's default index pointed at PyPI. If you've previously run `pip config set global.index-url https://pypi.fury.io/...`, reset it with `pip config unset global.index-url` (or set it back to `https://pypi.org/simple`) before installing.

Windows (PowerShell):
```powershell
py -m pip install --user pipx
pipx ensurepath
pipx install omni-code --pip-args="--extra-index-url https://pypi.fury.io/ericmichael/"
omni model setup
omni
```

### Option B: uv tool install
```bash
python3 -m pip install --user uv
uv tool install omni-code --extra-index-url https://pypi.fury.io/ericmichael/
omni model setup
omni
```

> If your pip configuration overrides the default index, reset it as described above so PyPI remains available.

Windows (PowerShell):
```powershell
py -m pip install --user uv
uv tool install omni-code --extra-index-url https://pypi.fury.io/ericmichael/
omni model setup
omni
```

The setup wizard guides you through choosing your provider (OpenAI, Azure, OpenAI-compatible, or LiteLLM), securely collects required keys, and writes them to a global config file:
- macOS/Linux: `~/.config/omni_code/models.json`
- Windows: `%APPDATA%\OmniCode\models.json`

You can rerun `omni model setup` anytime to update credentials. After setup, run `omni` from any directory to start the assistant (it auto-launches the setup wizard if no model is configured). By default it launches in the `ink` TUI; use `--mode` to switch (`web` or `server`).

If you prefer environment variables, you can skip the wizard and just set `OPENAI_API_KEY` (the built-in OpenAI provider profile references it via `${OPENAI_API_KEY}`).

Model switching uses references like `gpt-5.1` (defaults to the `openai` provider profile) or `azure-prod/gpt-5.2` for a named Azure profile.

### Updating

If you installed via pipx, you can upgrade with:

```bash
omni update
```

Non-interactive:

```bash
omni update --yes
```

> When the wizard prompts for API keys it uses hidden input, so you will not see characters or `***` while typing.

### Using Azure OpenAI
- Run `omni model setup` and choose the Azure option to provide your endpoint, deployment, API version, and API key.
- Manage models later with:
  - `omni model list`
  - `omni model default <name>`
  - `omni model show <name>`
- Advanced users can reference environment variables from `models.json` using `${VAR_NAME}` instead of storing keys directly.

## Developer Setup

### Project layout
```
.
├── main.py
├── tools/
├── omni_agents/
├── server_functions/
├── omni_code/
├── tests/
├── docs/
├── project.yml
├── pyproject.toml
└── requirements.txt
```

### Prerequisites
- Python 3.10+
- Go 1.20+
- libgit2 (e.g., `brew install libgit2`, `sudo apt-get install -y libgit2-dev`)
- Run `omni model setup` once to capture credentials in your global config if you have not already.
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
omni model setup
omni
```

> Development environments should also retain `https://pypi.org/simple` as the primary index. Use `pip config unset global.index-url` if Gemfury was set globally.

Run `omni model setup` again anytime you need to update credentials. To use the CLI globally without activating the virtual environment, follow either the pipx or uv instructions above.


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
