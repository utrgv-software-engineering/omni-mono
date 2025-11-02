#!/usr/bin/env python3
"""Simple test client for the realtime voice agent.

This client demonstrates basic interaction using text input.
For full voice interaction, see the README.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import omniagents
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from omniagents.rpc import JsonRpcClient
from omniagents.rpc.base import RpcMethodsBase


class RealtimeEventHandler(RpcMethodsBase):
    """Handle incoming realtime events."""

    def __init__(self):
        self.event_count = 0
        self.transcripts = []

    def realtime_event(self, **event):
        """Handle realtime_event notifications."""
        event_type = event.get("type", "unknown")
        self.event_count += 1

        # Print event summary based on type
        if event_type == "realtime_agent_start":
            print("\n[Agent started]")

        elif event_type == "realtime_agent_end":
            print("[Agent finished]")

        elif event_type == "realtime_raw_model_event":
            # Access transcript deltas via raw model events
            model_event = event.get("model_event", {})
            if model_event.get("type") == "transcript_delta":
                delta = model_event.get("delta", "")
                if delta:
                    print(delta, end="", flush=True)

        elif event_type == "realtime_tool_start":
            tool_name = event.get("tool_name", "unknown")
            print(f"\n[Calling tool: {tool_name}]")

        elif event_type == "realtime_tool_end":
            tool_name = event.get("tool_name", "unknown")
            print(f"[Tool completed: {tool_name}]")

        elif event_type == "realtime_error":
            error = event.get("error_message", "Unknown error")
            print(f"\n✗ ERROR: {error}")

        elif event_type == "realtime_audio":
            # Audio event received (we're not playing it in this test)
            pass


async def test_realtime_agent():
    """Test the realtime agent with text input."""
    print("=" * 60)
    print("Realtime Voice Agent Test Client")
    print("=" * 60)
    print()

    # Create event handler
    handler = RealtimeEventHandler()

    # Connect to server using async context manager
    print("Connecting to ws://localhost:8000/ws/realtime...")

    try:
        # Create client with event handler
        from omniagents.rpc.transport.websocket import ClientWebSocketTransport

        transport = ClientWebSocketTransport("ws://localhost:8000/ws/realtime")
        client = JsonRpcClient(transport, methods=handler)

        async with client:
            print("✓ Connected")

            # Start session
            print("\nStarting session...")
            result = await client.call("start_session")
            session_id = result["session_id"]
            run_id = result["run_id"]
            print(f"✓ Session started")
            print(f"  Session ID: {session_id}")
            print(f"  Run ID: {run_id}")

            print("\n" + "=" * 60)
            print("Testing agent with sample queries...")
            print("=" * 60)

            # Test queries
            test_queries = [
                "What's the weather in San Francisco?",
                "Give me a 3-day forecast for New York",
            ]

            for i, query in enumerate(test_queries, 1):
                print(f"\n\n--- Test {i}/{len(test_queries)} ---")
                print(f"USER: {query}")

                # Send text input
                await client.call("send_text", session_id=session_id, text=query)

                # Wait for response
                await asyncio.sleep(5)

            # Summary
            print("\n\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            print(f"Total events received: {handler.event_count}")
            print()

            # Stop session
            print("Stopping session...")
            await client.call("stop_session", session_id=session_id)
            print("✓ Session stopped")

        print("\nTest complete!")

    except Exception as e:
        print(f"✗ Error: {e}")
        print("\nMake sure the server is running:")
        print("  python server.py")
        import traceback

        traceback.print_exc()


async def test_session_management():
    """Test session management functions."""
    print("\n\n" + "=" * 60)
    print("Testing Session Management")
    print("=" * 60)

    try:
        # Create client without event handler for management tasks
        from omniagents.rpc.transport.websocket import ClientWebSocketTransport

        transport = ClientWebSocketTransport("ws://localhost:8000/ws/realtime")
        client = JsonRpcClient(transport)

        async with client:
            print("✓ Connected")

            # Start a session
            result = await client.call("start_session")
            session_id = result["session_id"]
            print(f"\nCreated session: {session_id}")

            # Get session info
            info = await client.call("get_session_info", session_id=session_id)
            print(f"\nSession info:")
            print(f"  Session ID: {info['session_id']}")
            print(f"  Run ID: {info['run_id']}")
            print(f"  Active: {info['active']}")

            # List sessions
            result = await client.call("list_sessions")
            print(f"\nActive sessions: {len(result['sessions'])}")
            for session in result["sessions"]:
                print(f"  - {session['session_id']} (active: {session['active']})")

            # Stop session
            await client.call("stop_session", session_id=session_id)
            print(f"\nStopped session: {session_id}")

            # List sessions again
            result = await client.call("list_sessions")
            print(f"Active sessions after stop: {len(result['sessions'])}")

        print("\nSession management test complete!")

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback

        traceback.print_exc()


async def main():
    """Run all tests."""
    try:
        # Test basic interaction
        await test_realtime_agent()

        # Test session management
        await test_session_management()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
