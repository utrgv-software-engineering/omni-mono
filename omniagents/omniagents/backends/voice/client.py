"""Voice client for realtime agent interaction.

This client supports:
- Microphone input capture
- Real-time audio streaming to agent
- Agent audio playback through speakers
- Transcript display
- Push-to-talk interaction (hold SPACEBAR)
- Graceful interruption
"""

import asyncio
import base64
import sys
import signal
from typing import Optional

try:
    import pyaudio
except ImportError:
    print("ERROR: PyAudio not installed")
    print("Install with: pip install pyaudio")
    print("\nOn macOS, you may need: brew install portaudio")
    print("On Linux: sudo apt-get install portaudio19-dev")
    sys.exit(1)

try:
    from pynput import keyboard
except ImportError:
    print("ERROR: pynput not installed")
    print("Install with: pip install pynput")
    sys.exit(1)

from omniagents.rpc import JsonRpcClient
from omniagents.rpc.base import RpcMethodsBase
from omniagents.rpc.transport.websocket import ClientWebSocketTransport


# Audio configuration (must match agent config in agent.yaml)
RATE = 24000  # Sample rate for pcm16
CHANNELS = 1  # Mono
FORMAT = pyaudio.paInt16  # 16-bit PCM
CHUNK = 4800  # 200ms chunks at 24kHz (4800 frames = 200ms at 24kHz)
INPUT_CHUNK = 2400  # 100ms input chunks for lower latency

# Push-to-talk settings
PUSH_TO_TALK_KEY = keyboard.Key.space  # Key to hold while speaking


class ClientNotificationHandler(RpcMethodsBase):
    """Handles notifications from the server."""

    def __init__(self, debug: bool = False):
        super().__init__()
        self.audio_queue = asyncio.Queue()
        self.transcripts = []
        self.is_speaking = False  # Push-to-talk state
        self.send_silence_chunks = (
            0  # Number of silence chunks to send after releasing key
        )
        self.debug = debug

    async def realtime_event(self, **params):
        """Handle realtime event notifications."""
        event_type = params.get("type")

        # Debug: print all events to see what we're getting
        if self.debug and event_type not in ["realtime_raw_model_event"]:
            print(f"\n[DEBUG] Event: {event_type}")

        if event_type == "realtime_audio":
            # Queue audio for playback
            audio_base64 = params.get("audio_base64", "")
            if audio_base64:
                if self.debug:
                    print(
                        f"[DEBUG] Received audio chunk: {len(audio_base64)} bytes (base64)"
                    )
                await self.audio_queue.put(audio_base64)

        elif event_type == "realtime_transcript":
            # Display transcript
            role = params.get("role", "")
            transcript = params.get("transcript", "")
            if transcript:
                self.transcripts.append((role, transcript))
                print(f"\n{role.upper()}: {transcript}")

        elif event_type == "realtime_tool_start":
            # Show tool being called with arguments
            tool_name = params.get("tool_name", "")
            arguments = params.get("arguments", "")
            print(f"\n🔧 [Calling tool: {tool_name}]")
            if arguments:
                # Pretty print arguments (try to parse as JSON for better formatting)
                try:
                    import json

                    args_dict = json.loads(arguments)
                    args_str = json.dumps(args_dict, indent=2)
                    print(f"   Arguments: {args_str}")
                except:
                    # Not valid JSON, just print as-is
                    print(f"   Arguments: {arguments}")

        elif event_type == "realtime_tool_end":
            # Show tool completed
            tool_name = params.get("tool_name", "")
            output = params.get("output", "")
            # Show first 100 chars of output
            output_preview = output[:100] + "..." if len(output) > 100 else output
            print(f"✓ [Tool completed: {tool_name}]")
            if output_preview:
                print(f"  Result: {output_preview}")

        elif event_type == "realtime_error":
            # Show errors
            error = params.get("error", "")
            print(f"\n✗ ERROR: {error}")


class VoiceClient:
    """Real-time voice client for agent interaction."""

    def __init__(
        self,
        server_url: str,
        auth_token: Optional[str] = None,
        session_id: Optional[str] = None,
        debug: bool = False,
    ):
        """Initialize voice client.

        Args:
            server_url: WebSocket URL (e.g., ws://localhost:8000/ws/realtime)
            auth_token: Optional authentication token
            session_id: Optional session ID to resume
            debug: Enable debug logging
        """
        self.server_url = server_url
        self.auth_token = auth_token
        self.session_id = session_id
        self.debug = debug
        self.p = None
        self.input_stream = None
        self.output_stream = None
        self.running = False
        self.client = None
        self.handler = ClientNotificationHandler(debug=debug)
        self.keyboard_listener = None

    def on_press(self, key):
        """Handle key press."""
        if key == PUSH_TO_TALK_KEY:
            if not self.handler.is_speaking:
                self.handler.is_speaking = True
                print("\n🎤 Recording... (release SPACE to stop)", end="", flush=True)

    def on_release(self, key):
        """Handle key release."""
        if key == PUSH_TO_TALK_KEY:
            if self.handler.is_speaking:
                self.handler.is_speaking = False
                self.handler.send_silence_chunks = (
                    10  # Send 10 chunks of silence (~200ms)
                )
                print("\n⏸  Released (waiting for response...)", end="", flush=True)

    async def start(self, agent_name: str = "Voice Agent"):
        """Start the voice client."""
        print("=" * 60)
        print(f"{agent_name}")
        print("=" * 60)
        print()

        # Initialize PyAudio
        print("Initializing audio...")
        self.p = pyaudio.PyAudio()

        # List available audio devices
        print("\nAvailable audio devices:")
        try:
            default_input = self.p.get_default_input_device_info()
            default_output = self.p.get_default_output_device_info()
            print(f"  Input: {default_input['name']}")
            print(f"  Output: {default_output['name']}")
        except Exception as e:
            print(f"  Warning: Could not get device info: {e}")

        # Open output stream (for agent audio)
        try:
            self.output_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                output=True,
                frames_per_buffer=CHUNK,
            )
            print("\n✓ Speaker initialized")
        except Exception as e:
            print(f"\n✗ Failed to open speaker: {e}")
            return False

        # Open input stream (for microphone)
        try:
            self.input_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=INPUT_CHUNK,
            )
            print("✓ Microphone initialized")
        except Exception as e:
            print(f"✗ Failed to open microphone: {e}")
            return False

        # Build WebSocket URL with optional auth token
        ws_url = self.server_url
        if self.auth_token:
            ws_url = f"{ws_url}?token={self.auth_token}"

        # Connect to server with notification handler
        print(f"\nConnecting to server...")
        try:
            transport = ClientWebSocketTransport(ws_url)
            self.client = JsonRpcClient(transport, methods=self.handler)
            await self.client.__aenter__()
            print("✓ Connected to server")
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            print("\nMake sure the server is running and the URL is correct")
            return False

        # Start or resume session
        if self.session_id:
            print(f"\nResuming session {self.session_id}...")
            try:
                result = await self.client.call(
                    "resume_session", session_id=self.session_id
                )
                self.session_id = result.get("session_id", self.session_id)
                print(f"✓ Session resumed: {self.session_id}")
            except Exception as e:
                print(f"✗ Failed to resume session: {e}")
                print("Starting new session instead...")
                try:
                    result = await self.client.call("start_session")
                    self.session_id = result["session_id"]
                    print(f"✓ New session started: {self.session_id}")
                except Exception as e:
                    print(f"✗ Failed to start session: {e}")
                    return False
        else:
            print("\nStarting voice session...")
            try:
                result = await self.client.call("start_session")
                self.session_id = result["session_id"]
                print(f"✓ Session started: {self.session_id}")
            except Exception as e:
                print(f"✗ Failed to start session: {e}")
                return False

        # Start keyboard listener for push-to-talk
        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_press, on_release=self.on_release
        )
        self.keyboard_listener.start()

        print("\n" + "=" * 60)
        print("Voice session active! (Push-to-Talk Mode)")
        print("=" * 60)
        print("\n📢 HOLD SPACEBAR to speak, release to stop.")
        print("Press Ctrl+C to end the session.\n")
        print("=" * 60)

        self.running = True
        return True

    async def play_agent_audio(self):
        """Play audio received from agent."""
        try:
            while self.running:
                try:
                    # Get audio from queue (with timeout)
                    audio_base64 = await asyncio.wait_for(
                        self.handler.audio_queue.get(), timeout=0.1
                    )

                    # Decode base64 audio
                    audio_bytes = base64.b64decode(audio_base64)
                    if self.debug:
                        print(f"[DEBUG] Playing audio: {len(audio_bytes)} bytes")

                    # Play through speakers
                    self.output_stream.write(audio_bytes)
                    if self.debug:
                        print(f"[DEBUG] Audio written to speaker")

                except asyncio.TimeoutError:
                    # No audio available, continue waiting
                    continue
                except Exception as e:
                    if self.running:
                        print(f"\n✗ Audio playback error: {e}")
                    break

        except asyncio.CancelledError:
            pass

    async def stream_microphone(self):
        """Stream microphone audio to the agent (push-to-talk)."""
        try:
            while self.running:
                try:
                    # Read from microphone (always read to prevent buffer overflow)
                    audio_data = self.input_stream.read(
                        INPUT_CHUNK, exception_on_overflow=False
                    )

                    # Check if we need to send silence chunks after releasing key
                    if self.handler.send_silence_chunks > 0:
                        # Send silence (zeros) to trigger server VAD
                        silence = b"\x00" * len(audio_data)
                        silence_base64 = base64.b64encode(silence).decode()
                        await self.client.call(
                            "send_audio",
                            session_id=self.session_id,
                            audio_base64=silence_base64,
                        )
                        self.handler.send_silence_chunks -= 1
                        await asyncio.sleep(0.01)
                        continue

                    # Only send audio when spacebar is held down
                    if not self.handler.is_speaking:
                        await asyncio.sleep(0.01)
                        continue

                    # Encode to base64
                    audio_base64 = base64.b64encode(audio_data).decode()

                    # Send to agent (server VAD will detect when you stop)
                    await self.client.call(
                        "send_audio",
                        session_id=self.session_id,
                        audio_base64=audio_base64,
                    )

                    # Small delay to prevent overwhelming the server
                    await asyncio.sleep(0.01)

                except Exception as e:
                    if self.running:
                        print(f"\n✗ Microphone error: {e}")
                    break

        except asyncio.CancelledError:
            pass

    async def run(self, agent_name: str = "Voice Agent"):
        """Run the voice client main loop."""
        if not await self.start(agent_name=agent_name):
            return

        # Start background tasks
        tasks = [
            asyncio.create_task(self.stream_microphone()),
            asyncio.create_task(self.play_agent_audio()),
        ]

        try:
            # Run until interrupted
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup
            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            await self.stop()

    async def stop(self):
        """Stop the voice client and clean up resources."""
        print("\n\nStopping voice session...")
        self.running = False

        # Stop keyboard listener
        if self.keyboard_listener:
            self.keyboard_listener.stop()

        # Stop session on server
        if self.client and self.session_id:
            try:
                await self.client.call("stop_session", session_id=self.session_id)
                print("✓ Session stopped")
            except Exception as e:
                print(f"✗ Error stopping session: {e}")

        # Close client
        if self.client:
            try:
                await self.client.__aexit__(None, None, None)
            except Exception:
                pass

        # Close audio streams
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()

        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()

        # Terminate PyAudio
        if self.p:
            self.p.terminate()

        print("\nGoodbye!")


def run_voice_client(
    server_url: str,
    agent_name: str = "Voice Agent",
    auth_token: Optional[str] = None,
    session_id: Optional[str] = None,
    debug: bool = False,
):
    """Main entry point for voice client.

    Args:
        server_url: WebSocket URL (e.g., ws://localhost:8000/ws/realtime)
        agent_name: Display name for the agent
        auth_token: Optional authentication token
        session_id: Optional session ID to resume
        debug: Enable debug logging
    """

    async def async_main():
        # Create and run client
        client = VoiceClient(
            server_url=server_url,
            auth_token=auth_token,
            session_id=session_id,
            debug=debug,
        )

        # Handle Ctrl+C gracefully
        def signal_handler(sig, frame):
            print("\n\nInterrupt received, shutting down...")
            client.running = False

        signal.signal(signal.SIGINT, signal_handler)

        try:
            await client.run(agent_name=agent_name)
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
        except Exception as e:
            print(f"\n✗ Error: {e}")
            if debug:
                import traceback

                traceback.print_exc()
        finally:
            if client.running:
                await client.stop()

    asyncio.run(async_main())
