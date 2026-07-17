# Omni Code

A standalone coding agent built with OmniAgents. It gives an AI assistant safe access to your local workspace and a rich set of tools to search, edit, and run code; fetch docs; and summarize content.

## Features
- Rich workspace tools: list, search, read, edit, and patch files; run shell commands
- Web and docs tools: fetch web pages, Google/YouTube/Scholar via SerpAPI, download files, convert many formats to Markdown
- Optional safe-guarded execution with approvals
- Ink TUI and web runner (default mode: `ink`)

## Modes

The `omni` CLI defaults to `--mode ink`. You can pass through any `omniagents run` flags.

Examples:

```bash
omni                     # Ink TUI
omni --mode web          # Web runner
omni --mode server       # Run the backend server
```

For launcher/embed scenarios, use:

```bash
omni --embedded --mode web --ui-minimal
```

In this mode Omni Code does not auto-open a browser and emits a machine-readable JSON line from the web backend with the resolved `url` and `port`.

## Sandbox (Docker)

If you have Docker installed, you can run Omni Code inside a container while mounting your current workspace and your Omni Code config directory (models/MCP/skills):

```bash
omni sandbox --ui container
```

To persist credentials or tool state between runs, mount a persistent Docker volume into the container:

```bash
omni sandbox --persist-volume omni-cred-azure:/home/user/.azure
```

To run `code-server` inside the sandbox and expose it on a host port:

```bash
omni sandbox --ui container --enable-code-server --code-server-port 8080
```

By default the sandbox persists OmniAgents state (sessions + Studio traces) in an isolated directory so sandbox history is not mixed with your local OmniAgents history:

- Host: `~/.config/omni_code/sandbox/omniagents/` → Container: `/home/user/.omniagents/`

You can override this location (or intentionally share your local OmniAgents state) with:

```bash
omni sandbox --omniagents-home ~/.omniagents
```

For Electron-style launching, you can run in the background and print only the URL:

```bash
omni sandbox --mode web --detach --print-url --port 0
```

For programmatic launching (e.g. an Electron wrapper), you can emit a machine-readable JSON payload:

```bash
omni sandbox --ui local --enable-code-server --enable-vnc --code-server-port 0 --vnc-port 0 --ui-port 0 --output json
```

For `--ui container/none`, JSON output requires `--detach`.

By default it will pass `<workspace>/.env` to Docker if present. You can override it with:

```bash
omni sandbox --env-file /path/to/.env
```

## In-session commands

Type these while the assistant is running:

- `/help` - Show available commands
- `/models` - List available models and current session state
- `/model <name>` - Switch models for this session
- `/reasoning <low|medium|high>` - Set reasoning effort level
- `/compact` - Compact conversation context
- `/exit` - End the current session
- `/good` / `/bad` / `/note` - Feedback and session notes

## Sessions (CLI)

Inspect prior conversations stored in the OmniAgents sessions database:

```bash
omni sessions list --pretty
omni sessions list --after 2026-02-01 --limit 20 --offset 0 --pretty
omni sessions list --after 2026-02-01 --limit 20 --offset 20 --pretty
omni sessions list --stats --limit 20 --pretty
omni sessions search "kubernetes" --role user --pretty
omni sessions search "kubernetes" --after 2026-02-01 --limit 20 --offset 0 --pretty
omni sessions export <session_id> --format jsonl
omni sessions summarize <session_id> --pretty
```

## API keys

- OpenAI: set `OPENAI_API_KEY` (or run `omni model setup` to store credentials in the global config).
- SerpAPI (web/video/scholar search tools): set `SERPAPI_API_KEY`.

## Skills

Omni Code supports Agent Skills by injecting discovered skills into the system prompt.

- Global: `~/.config/omni_code/skills/` (Windows: `%APPDATA%\OmniCode\skills\`)
- Per-workspace: `<workspace_root>/.omni_code/skills/`

## End-User Install
- Python 3.10+ and libgit2 must be available on your PATH.
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

## Voice agent

This project also includes a voice-optimized agent spec (`voice_agent`). How you select it depends on the OmniAgents runner/UI you use; look for an agent selection option and choose `voice_agent`.

## Troubleshooting

- `No model credentials configured`: run `omni model setup`.
- `ImportError: libgit2` / `pygit2` failures: install `libgit2` (macOS: `brew install libgit2`, Debian/Ubuntu: `sudo apt-get install -y libgit2-dev`).

## Developer Setup

### Project layout
```
.
├── main.py
├── omni_code/            # the installable product package
│   ├── project.yml       # project manifest (paths relative to it)
│   ├── agents/           # agent specs + instructions + context factories
│   ├── tools/            # product-specific tools
│   ├── capabilities/     # capability-pack overlays
│   ├── server_functions/ # product server functions
│   └── overlays/         # project.yml overlays (e.g. platform security)
├── evaluations/          # eval scenarios/measures (repo-only, not packaged)
├── environments/         # eval execution environments (repo-only)
├── tests/
├── docs/
├── pyproject.toml
└── requirements.txt
```

### Prerequisites
- Python 3.10+
- libgit2 (e.g., `brew install libgit2`, `sudo apt-get install -y libgit2-dev`)
- Run `omni model setup` once to capture credentials in your global config if you have not already.
- For Azure OpenAI development, choose the Azure option inside the setup wizard to populate the required variables.

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
