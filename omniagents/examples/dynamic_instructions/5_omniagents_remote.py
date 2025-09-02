#!/usr/bin/env python
"""
Interactive remote client using Runner.from_remote() - cleaner API.

First start the server:
    python examples/dynamic_instructions/3_omniagents_function.py --mode server --port 8000

Then run this client:
    python examples/dynamic_instructions/remote_client_2.py
"""

from omniagents import Runner

def main():
    # Create variables that will be sent to the server
    # These are initial variables, not the full context - the server
    # will construct the actual typed context from these
    variables = {
        "user_name": "Alice Johnson",
        "age": 28,
        "location": "Seattle",
        # Extra fields that might be used by tools but not in the dataclass
        "hotel_id": "hilton_sea_456",
        "user_id": "user_9999"
    }
    
    print(f"Sending initial variables to remote agent: {variables}")
    print("=" * 60)
    
    # Use from_remote - context here is really initial variables
    # The server will use its build_context factory to convert these
    # variables into the proper typed context
    runner = Runner.from_remote(
        url="ws://0.0.0.0:8000/ws",
        name="Remote Assistant",
        welcome_text="Connected to your personalized assistant!",
        context=variables  # Will be passed as 'variables' over RPC
    )
    
    # Run in CLI mode
    runner.main(default_mode="cli")

if __name__ == "__main__":
    main()