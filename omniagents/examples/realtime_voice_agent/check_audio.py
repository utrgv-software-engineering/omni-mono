#!/usr/bin/env python3
"""Check audio setup for voice client.

This script verifies that PyAudio is installed and lists available audio devices.
"""

import sys

print("Checking audio setup...\n")

# Check PyAudio installation
try:
    import pyaudio

    print("✓ PyAudio is installed")
except ImportError:
    print("✗ PyAudio is NOT installed")
    print("\nInstall with:")
    print("  pip install pyaudio")
    print("\nSystem dependencies:")
    print("  macOS: brew install portaudio")
    print("  Linux: sudo apt-get install portaudio19-dev")
    sys.exit(1)

# List audio devices
print("\nInitializing PyAudio...")
p = pyaudio.PyAudio()

print(f"\nFound {p.get_device_count()} audio devices:\n")

# Get default devices
try:
    default_input = p.get_default_input_device_info()
    default_input_idx = default_input["index"]
except Exception:
    default_input_idx = None

try:
    default_output = p.get_default_output_device_info()
    default_output_idx = default_output["index"]
except Exception:
    default_output_idx = None

# List all devices
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)

    device_type = []
    if i == default_input_idx:
        device_type.append("DEFAULT INPUT")
    elif info["maxInputChannels"] > 0:
        device_type.append("input")

    if i == default_output_idx:
        device_type.append("DEFAULT OUTPUT")
    elif info["maxOutputChannels"] > 0:
        device_type.append("output")

    type_str = f" ({', '.join(device_type)})" if device_type else ""

    print(f"  {i}: {info['name']}{type_str}")
    print(f"     Channels: in={info['maxInputChannels']}, out={info['maxOutputChannels']}")
    print(f"     Sample rate: {info['defaultSampleRate']:.0f} Hz")
    print()

# Test recording
if default_input_idx is not None:
    print(f"\n{'='*60}")
    print("Testing microphone...")
    print(f"{'='*60}\n")

    try:
        # Open input stream
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            input=True,
            frames_per_buffer=2400,
        )

        print(f"✓ Successfully opened microphone: {default_input['name']}")
        print("  Recording a 1-second test...")

        # Record briefly
        frames = []
        for _ in range(10):  # 10 x 100ms = 1 second
            data = stream.read(2400, exception_on_overflow=False)
            frames.append(data)

        print("  ✓ Recording successful")

        stream.stop_stream()
        stream.close()

    except Exception as e:
        print(f"  ✗ Microphone test failed: {e}")

# Test playback
if default_output_idx is not None:
    print(f"\n{'='*60}")
    print("Testing speakers...")
    print(f"{'='*60}\n")

    try:
        # Open output stream
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True,
            frames_per_buffer=4800,
        )

        print(f"✓ Successfully opened speakers: {default_output['name']}")

        stream.stop_stream()
        stream.close()

    except Exception as e:
        print(f"  ✗ Speaker test failed: {e}")

p.terminate()

print(f"\n{'='*60}")
print("Audio check complete!")
print(f"{'='*60}\n")

if default_input_idx is not None and default_output_idx is not None:
    print("✓ Your system is ready for voice interaction")
    print("\nRun the voice client with:")
    print("  python voice_client.py")
else:
    if default_input_idx is None:
        print("✗ No default input device (microphone) found")
    if default_output_idx is None:
        print("✗ No default output device (speakers) found")

    print("\nPlease check your audio device connections.")
