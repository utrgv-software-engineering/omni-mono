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
    # Create context that will be passed with each message
    # This simulates what a web widget would pass
    context = {
        "user_name": "Alice Johnson",
        "age": 28,
        "location": "Seattle",
        "hotel_id": "hilton_sea_456",
        "user_id": "user_9999"
    }
    
    print(f"Connecting to remote agent with context: {context}")
    print("=" * 60)
    
    # Use the new from_remote method for cleaner API
    runner = Runner.from_remote(
        url="ws://0.0.0.0:8000/ws",
        name="Remote Assistant",
        welcome_text="Connected to your personalized assistant!",
        context=context
    )
    
    # Run in CLI mode
    runner.main(default_mode="cli")

if __name__ == "__main__":
    main()