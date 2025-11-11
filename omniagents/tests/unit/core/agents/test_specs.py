import pytest

from omniagents.core.agents.specs import HandoffConfig, RealtimeSettings


def test_handoff_config_requires_single_source_inline():
    with pytest.raises(ValueError):
        HandoffConfig()


def test_handoff_config_requires_single_source_both():
    with pytest.raises(ValueError):
        HandoffConfig(yaml_path="a.yaml", inline_spec=object())


def test_realtime_settings_defaults():
    """Test that RealtimeSettings has correct default values."""
    settings = RealtimeSettings()

    assert settings.model_name == "gpt-realtime"
    assert settings.voice == "alloy"
    assert settings.modalities == ["audio"]
    assert settings.input_audio_format == "pcm16"
    assert settings.output_audio_format == "pcm16"
    assert settings.turn_detection == {
        "type": "server_vad",
        "threshold": 0.3,
        "silence_duration_ms": 200,
        "prefix_padding_ms": 100,
    }
    assert settings.input_audio_transcription == {"model": "whisper-1"}
    assert settings.temperature == 0.8
    assert settings.max_output_tokens == 4096


def test_realtime_settings_custom_values():
    """Test that RealtimeSettings accepts custom values."""
    settings = RealtimeSettings(
        model_name="gpt-4o-mini-realtime-preview",
        voice="shimmer",
        modalities=["text", "audio"],
        input_audio_format="g711_ulaw",
        output_audio_format="g711_alaw",
        turn_detection={
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 700,
        },
        input_audio_transcription={"model": "whisper-1", "language": "en"},
        temperature=0.7,
        max_output_tokens=1000,
    )

    assert settings.model_name == "gpt-4o-mini-realtime-preview"
    assert settings.voice == "shimmer"
    assert settings.modalities == ["text", "audio"]
    assert settings.input_audio_format == "g711_ulaw"
    assert settings.output_audio_format == "g711_alaw"
    assert settings.turn_detection == {
        "type": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 700,
    }
    assert settings.input_audio_transcription == {
        "model": "whisper-1",
        "language": "en",
    }
    assert settings.temperature == 0.7
    assert settings.max_output_tokens == 1000


def test_realtime_settings_partial_config():
    """Test RealtimeSettings with partial configuration."""
    settings = RealtimeSettings(
        voice="echo", temperature=0.3, turn_detection={"type": "semantic_vad"}
    )

    # Custom values
    assert settings.voice == "echo"
    assert settings.temperature == 0.3
    assert settings.turn_detection == {"type": "semantic_vad"}

    # Defaults
    assert settings.model_name == "gpt-realtime"
    assert settings.modalities == ["audio"]
    assert settings.input_audio_format == "pcm16"
