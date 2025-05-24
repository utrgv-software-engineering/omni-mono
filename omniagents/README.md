# OpenAI Agents Python Project

This project leverages the OpenAI Agents Python library to create a versatile application featuring multiple tools for various real-world applications.

## Overview

The project provides a platform to integrate multiple specialized tools, allowing for interaction with external APIs and services, including weather updates, web searches, geocoding, and more.

## Setup

### Option 1: Using pip (Recommended)

Install the dependencies listed in `requirements.txt` and run the scripts normally.

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment variables**:
   Copy `.env.example` to create `.env`, and edit it to add your OpenAI API key:
   ```bash
   cp .env.example .env
   # Edit .env with your favorite editor
   ```

3. **Run example scripts**:
   ```bash
   python main.py
   python surfing_agent.py
   python advanced_example.py
   ```

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

- `examples/basic_python` – simple agent defined directly in Python.
- `examples/basic_yaml` – simple agent defined in YAML.
- `examples/safe_agent_python` – SafeAgent demo written in Python that asks for approval before using tools.
- `examples/safe_agent_yaml` – SafeAgent demo configured in YAML.
- `examples/remote_agent` – run an agent as a JSON-RPC server and connect from another process.
- `examples/agentdeck` – Flutter project scaffolding.

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

## Running Tests

Install dependencies and run the test suite with `pytest`:

```bash
pip install -r requirements.txt
pytest -q
```
