# OpenAI Agents Python Project

This project leverages the OpenAI Agents Python library to create a versatile application featuring multiple tools for various real-world applications.

## Overview

The project provides a platform to integrate multiple specialized tools, allowing for interaction with external APIs and services, including weather updates, web searches, geocoding, and more.

## Setup

### Option 1: Using uv (Recommended)

The project uses `pyproject.toml` for dependency management with uv, allowing for a simpler developer experience with no environment setup.

1. **Install uv** (if not already installed):
   ```shell
   # macOS (Homebrew)
   brew install astral/tap/uv
   
   # Windows (via PowerShell)
   irm https://astral.sh/uv/install.ps1 | iex
   
   # Linux/macOS (via curl)
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Using pip (alternative)
   pip install uv
   ```

2. **Configure environment variables**:
   Copy `.env.example` to create `.env`, and edit it to add your OpenAI API key:
   ```shell
   cp .env.example .env
   # Edit .env with your favorite editor
   ```

3. **Run scripts with uv**:
   ```shell
   # Run any script directly - uv handles all dependencies automatically
   uv run main.py
   uv run surfing_agent.py
   uv run advanced_example.py
   ```

That's it! uv automatically:
- Creates an isolated environment for each run
- Installs all dependencies defined in `pyproject.toml`
- Runs your script without requiring environment activation
- Caches dependencies for faster subsequent runs

### Option 2: Using conda (Legacy)

1. **Create the conda environment**:
   ```shell
   conda env create -f environment.yml
   ```

2. **Activate the environment**:
   ```shell
   conda activate openai-agents
   ```

3. **Configure environment variables**:
   Copy `.env.example` to create `.env`, and edit it to add your OpenAI API key.

4. **Run example scripts**:
   ```shell
   python main.py             # Basic example
   python advanced_example.py # Interactive example with multiple tools
   ```

## Tools Included

- **bash_tool.py**: Execute and manage bash commands.
- **file_tools.py**: Perform various file operations.
- **geocoding.py**: Convert addresses into geographic coordinates.
- **google_flights.py**: Interface with Google Flights for search and data retrieval.
- **scholar_search.py**: Search scholarly articles and databases.
- **weather.py**: Fetch current weather information.
- **web_search.py**: Conduct online searches.
- **youtube_search.py**: Search for YouTube videos.

## Environment Variables

Configure the following variables in your `.env` file:

- `OPENAI_API_KEY` (required): Your OpenAI API key
- `OPENAI_API_BASE` (optional): Custom API base URL (defaults to `https://api.openai.com/v1`)
- `MODEL_NAME` (optional): The model to use (defaults to `gpt-4-turbo`)

## Examples

- `main.py`: Basic implementation with a weather tool.
- `advanced_example.py`: Interactive example integrating multiple tools for a dynamic chat interface.

## Resuming Sessions

Conversation history is saved in a local SQLite file `sessions.db`. Each chat is
identified by a session ID.

### CLI

Use `--session-id <id>` with `omniagents cli run --mode cli` to continue a
previous conversation. If omitted, a new ID is generated and printed at startup.

### GUI

GUI sessions always start fresh. Conversation history is not automatically
restored when reopening the app.

## Documentation

For more information, see the [OpenAI Agents Python documentation](https://openai.github.io/openai-agents-python/quickstart/).
