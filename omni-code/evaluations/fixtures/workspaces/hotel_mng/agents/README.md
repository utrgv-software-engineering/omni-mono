# Hotel AI Agents

This directory contains AI agents that integrate with the hotel management system.

## Architecture

The agents use the `omniagents` framework and communicate with the Rails application via:
1. **WebSocket RPC** - Real-time bidirectional communication for chat interfaces
2. **REST API** - HTTP calls to Rails API endpoints for data operations

## Available Agents

### hotel_chat
Customer-facing chatbot that helps with:
- Checking room availability
- Viewing room types and prices
- Making new reservations
- Looking up existing reservations
- Providing hotel information

## Setup

### Prerequisites

1. Ensure Python 3 is installed:
```bash
python3 --version
```

2. Install Python dependencies:
```bash
cd agents
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or run the Rails setup script which handles everything:
```bash
bin/setup
```

### Environment Variables

Create a `.env` file in the Rails root with:
```env
# API credentials for agents to access Rails API
HOTEL_API_KEY=your-api-key-here
HOTEL_ID=1

# Optional: Override agent URLs (defaults to localhost in development)
HOTEL_CHAT_AGENT_URL=ws://localhost:8001
```

## Running Agents

### Development (All services together)
```bash
bin/dev  # Starts Rails, Tailwind CSS watcher, and all agents
```

### Individual Agent
```bash
cd agents
source .venv/bin/activate  # Activate virtual environment
python hotel_chat/hotel_agent.py --mode server --port 8001
```

### CLI Mode (for testing)
```bash
cd agents
source .venv/bin/activate  # Activate virtual environment
python hotel_chat/hotel_agent.py --mode cli
```

## Agent Configuration

Agents are configured in `config/ai_deployments.yml`:
- **Platform agents** - Always enabled, system-wide
- **Hotel agents** - Can be enabled/disabled per hotel

Hotels enable agents through the admin interface, which creates `HotelAiSetting` records.

## Development Workflow

1. **Modifying agent code**: Edit files in `agents/hotel_chat/`
2. **Adding dependencies**: Update `requirements.txt` and run `pip install -r requirements.txt`
3. **Testing changes**: The agent auto-reloads in server mode
4. **API changes**: Update tool functions in `agents/hotel_chat/tools/`

## Communication Flow

```
User Browser → ActionCable WebSocket → Rails AgentChatChannel
    ↓
Rails AgentChatJob (background)
    ↓
AgentRpcClient → WebSocket → Python Agent Server (port 8001)
    ↓
Agent processes with GPT model
    ↓
Agent calls Rails API endpoints (HTTP)
    ↓
Response flows back through WebSocket → ActionCable → Browser
```

## Adding New Agents

1. Create new directory: `agents/new_agent/`
2. Add agent implementation using omniagents framework
3. Update `Procfile.dev` to start the new agent
4. Add configuration to `config/ai_deployments.yml`
5. Create corresponding Rails channel/job if needed

## Troubleshooting

### Agent won't start
- Check Python 3 is installed: `python3 --version`
- Activate virtual environment: `source agents/.venv/bin/activate`
- Install dependencies: `pip install -r requirements.txt`
- Check port availability: `lsof -i :8001`

### Agent can't connect to Rails API
- Verify Rails is running: `curl http://localhost:3000/api/v1/health`
- Check API key in `.env`
- Verify HOTEL_ID exists in database

### WebSocket connection fails
- Check agent is running: `ps aux | grep hotel_agent`
- Verify URL in `config/ai_deployments.yml`
- Check browser console for WebSocket errors