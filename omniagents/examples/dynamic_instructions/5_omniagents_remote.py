#!/usr/bin/env python
"""
Interactive remote client using Runner.from_remote() - cleaner API.

First start the server with either example:
    python examples/dynamic_instructions/2_omniagents_string.py --mode server --port 8000
    OR
    python examples/dynamic_instructions/3_omniagents_function.py --mode server --port 8000

Then run this client:
    python examples/dynamic_instructions/5_omniagents_remote.py
"""

from omniagents import Runner


def main():
    # Only send user_id - server will fetch full user profile
    # This simulates a real-world scenario where:
    # - Client (web widget, mobile app) only has user_id from auth/session
    # - Server fetches complete user data from database/API
    variables = {
        "user_id": "user_001"  # Server will fetch Alice Johnson's full profile
    }

    print(f"Sending minimal variables to remote agent: {variables}")
    print("Server will fetch full user profile based on user_id")
    print("=" * 60)

    # Use from_remote with proper 'variables' parameter
    # The server will use its build_context factory to transform these variables
    runner = Runner.from_remote(
        url="ws://0.0.0.0:8000/ws",
        name="Remote Assistant",
        welcome_text="Connected to your personalized assistant!",
        variables=variables,  # Will be sent as 'variables' to server for transformation
    )

    # Run in CLI mode
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
