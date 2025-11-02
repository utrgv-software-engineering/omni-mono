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


async def test_realtime_agent():
    """Test the realtime agent with text input."""
    print("=" * 60)
    print("Realtime Voice Agent Test Client")
    print("=" * 60)
    print()

    # Connect to server
    print("Connecting to ws://localhost:8000/ws/realtime...")
    client = JsonRpcClient("ws://localhost:8000/ws/realtime")

    try:
        await client.connect()
        print("✓ Connected")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("\nMake sure the server is running:")
        print("  python server.py")
        return

    # Start session
    print("\nStarting session...")
    try:
        result = await client.call("start_session")
        session_id = result["session_id"]
        run_id = result["run_id"]
        print(f"✓ Session started")
        print(f"  Session ID: {session_id}")
        print(f"  Run ID: {run_id}")
    except Exception as e:
        print(f"✗ Failed to start session: {e}")
        await client.disconnect()
        return

    # Event handler
    event_count = {"count": 0}
    transcripts = []

    async def handle_events():
        """Handle incoming events from the agent."""
        async for notification in client.notifications():
            if notification["method"] == "realtime_event":
                event = notification["params"]
                event_type = event["type"]
                event_count["count"] += 1

                # Print event summary
                if event_type == "realtime_turn_started":
                    print("\n[Agent is thinking...]")

                elif event_type == "realtime_turn_ended":
                    print("[Agent finished]")

                elif event_type == "realtime_transcript":
                    role = event["role"]
                    text = event["transcript"]
                    transcripts.append((role, text))
                    print(f"\n{role.upper()}: {text}")

                elif event_type == "realtime_tool_called":
                    tool_name = event["tool_name"]
                    print(f"\n[Calling tool: {tool_name}]")

                elif event_type == "realtime_tool_completed":
                    tool_name = event["tool_name"]
                    print(f"[Tool completed: {tool_name}]")

                elif event_type == "realtime_tool_failed":
                    tool_name = event["tool_name"]
                    error = event["error"]
                    print(f"\n[Tool failed: {tool_name}]")
                    print(f"  Error: {error}")

                elif event_type == "realtime_error":
                    error = event["error"]
                    print(f"\n✗ ERROR: {error}")

    # Start event handler
    event_task = asyncio.create_task(handle_events())

    print("\n" + "=" * 60)
    print("Testing agent with sample queries...")
    print("=" * 60)

    # Test queries
    test_queries = [
        "What's the weather in San Francisco?",
        "Give me a 3-day forecast for New York",
        "Are there any weather alerts in London?",
    ]

    try:
        for i, query in enumerate(test_queries, 1):
            print(f"\n\n--- Test {i}/{len(test_queries)} ---")
            print(f"USER: {query}")

            # Send text input
            await client.call("send_text", {"session_id": session_id, "text": query})

            # Wait for response
            await asyncio.sleep(5)

    except Exception as e:
        print(f"\n✗ Error during interaction: {e}")

    # Summary
    print("\n\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Total events received: {event_count['count']}")
    print(f"Transcripts captured: {len(transcripts)}")
    print()

    # Stop session
    print("Stopping session...")
    try:
        await client.call("stop_session", {"session_id": session_id})
        print("✓ Session stopped")
    except Exception as e:
        print(f"✗ Failed to stop session: {e}")

    # Cleanup
    event_task.cancel()
    try:
        await event_task
    except asyncio.CancelledError:
        pass

    await client.disconnect()
    print("\nTest complete!")


async def test_session_management():
    """Test session management functions."""
    print("\n\n" + "=" * 60)
    print("Testing Session Management")
    print("=" * 60)

    client = JsonRpcClient("ws://localhost:8000/ws/realtime")
    await client.connect()

    # Start a session
    result = await client.call("start_session")
    session_id = result["session_id"]
    print(f"\nCreated session: {session_id}")

    # Get session info
    info = await client.call("get_session_info", {"session_id": session_id})
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
    await client.call("stop_session", {"session_id": session_id})
    print(f"\nStopped session: {session_id}")

    # List sessions again
    result = await client.call("list_sessions")
    print(f"Active sessions after stop: {len(result['sessions'])}")

    await client.disconnect()
    print("\nSession management test complete!")


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
