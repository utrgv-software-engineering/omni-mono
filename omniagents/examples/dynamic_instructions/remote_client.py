#!/usr/bin/env python
"""
Interactive remote client that passes dynamic context to the server.

First start the server:
    python examples/dynamic_instructions/2_omniagents_string.py --mode server --port 8000

Then run this client:
    python examples/dynamic_instructions/remote_client_interactive.py
"""

from omniagents import Runner
from omniagents.core.agents.specs import AgentSpec

def main():
    # Create context that will be passed with each message
    # This simulates what a web widget would pass
    context = {
        "user_name": "Jane Smith",
        "age": 42,
        "location": "Chicago",
        "hotel_id": "sheraton_chi_789",
        "user_id": "user_1337"
    }
    
    print(f"Connected to remote agent with context: {context}")
    print("=" * 60)
    
    # Create spec for remote agent
    spec = AgentSpec(
        name="Remote Client",
        header_title="Remote Client",
        page_title="Remote Client",
        welcome_text="Connected to remote assistant. Type your message:",
        client_storage_prefix="remote_client.",
        get_agent_instructions=lambda: "",
        build_agent=None,  # Not needed for remote agents
        remote_ws_url="ws://0.0.0.0:8000/ws",  # This tells it to use RemoteAgent
        context=context  # Pass the context through the spec
    )
    
    # Create runner with the spec
    runner = Runner(spec=spec)
    
    # Run in CLI mode
    runner.main(default_mode="cli")

if __name__ == "__main__":
    main()
