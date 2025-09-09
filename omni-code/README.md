# Omni Code

A standalone coding agent built with OmniAgents. It gives an AI assistant safe access to your local workspace and a rich set of tools to search, edit, and run code; fetch docs; and summarize content.

## Features
- Rich workspace tools: list, search, read, edit, and patch files; run shell commands
- Web and docs tools: fetch web pages, Google/YouTube/Scholar via SerpAPI, download files, convert many formats to Markdown
- Optional safe-guarded execution with approvals
- CLI and YAML-based configuration

## Project layout
```
.
├── agent.yaml          # YAML config (tools, model, safety)
├── instructions.md     # System prompt for the agent
├── main.py             # Python entry point
├── tools/              # Tool implementations (rich tools for OmniAgents UI)
├── tests/              # Unit tests for tools
├── pyproject.toml      # Package metadata (preferred)
└── requirements.txt    # Alternative installation method
```

## Prerequisites
- Python 3.10+
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

The default runner starts an OmniAgents CLI with safe mode enabled for destructive tools. See `agent.yaml` to adjust behavior.

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

If you prefer using the YAML runner, you can point the OmniAgents runner to `agent.yaml`.

## License
MIT (see LICENSE)
