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
pip install -e .

# Run the agent (CLI)
omni-code
# or
python main.py
```

The default runner starts an OmniAgents CLI with safe mode enabled for destructive tools. See `agent.yaml` to adjust behavior.

## Running tests
```
pip install -e .[dev]
pytest
```

## Packaging notes
This app depends on the OmniAgents framework:
- `omniagents` is pulled directly from GitHub
- Tools are decorated with `@rich_function_tool` and return `RichToolOutput` for a pleasant UI

If you prefer using the YAML runner, you can point the OmniAgents runner to `agent.yaml`.

## License
MIT (see LICENSE)
