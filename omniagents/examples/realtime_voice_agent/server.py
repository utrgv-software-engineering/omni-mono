#!/usr/bin/env python3
"""Simple server for the realtime voice agent example.

This script starts a FastAPI server with the realtime voice agent.
Connect to ws://localhost:8000/ws/realtime to interact with the agent.
"""

import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

# Add parent directory to path to import omniagents
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.rpc import JsonRpcEndpoint
from omniagents.rpc.realtime_service import RealtimeService


def create_app():
    """Create and configure the FastAPI app."""
    # Load agent spec
    config_path = Path(__file__).parent / "agent.yaml"
    spec = load_agent_spec_from_yaml(str(config_path))

    # Build settings
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set")
        print("Please set it with: export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    settings = {"openai_api_key": api_key}

    # Create service and endpoint
    service = RealtimeService(spec, settings)
    endpoint = JsonRpcEndpoint(service)

    # Create FastAPI app
    app = FastAPI(
        title="Realtime Voice Agent Example",
        description="Voice-enabled weather assistant using OpenAI's realtime API",
        version="1.0.0",
    )

    # Register realtime endpoint
    endpoint.register_route(app, "/ws/realtime")

    @app.get("/")
    async def root():
        return {
            "message": "Realtime Voice Agent Server",
            "endpoint": "/ws/realtime",
            "instructions": "Connect a WebSocket client to /ws/realtime to start a voice session",
        }

    return app


def main():
    """Run the server."""
    print("=" * 60)
    print("Realtime Voice Agent Server")
    print("=" * 60)
    print("\nEndpoint: ws://localhost:8000/ws/realtime")
    print("\nPress Ctrl+C to stop\n")
    print("=" * 60)

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
